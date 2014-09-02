import copy
import json
import logging
import sys
import time
import traceback
from functools import wraps
from itertools import izip_longest
from unicodedata import east_asian_width

from boto.s3.connection import S3Connection, Location as S3Location
from boto.s3.key import Key

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from xmodule.contentstore.content import StaticContent
from xmodule.contentstore.django import contentstore
from xmodule.exceptions import NotFoundError
from xmodule.modulestore.django import modulestore, ModuleI18nService
from xmodule.modulestore import Location
from xmodule.video_module import transcripts_utils
from xmodule.video_module.transcripts_utils import GetTranscriptsFromYouTubeException


log = logging.getLogger(__name__)


def handle_exception(func):
    """
    Command Exception Hander

    If the command failed, write 'NG' to the specific file (for monitoring).
    Otherwise, write 'OK'.
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            out = func(*args, **kwargs)
            # Command OK
            output_command_status('OK')
            return out
        except CommandError as e:
            # Command NG
            output_command_status('NG', traceback.format_exception(*sys.exc_info())[-1])
            raise e
        except Exception as e:
            log.error("Command update_transcripts failed unexpectedly.\n%s" % traceback.format_exc())
            # Command NG
            output_command_status('NG', traceback.format_exception(*sys.exc_info())[-1])
            raise e
    return wrapper


def output_command_status(status, msg=''):
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())
    with open(settings.TRANSCRIPTS_COMMAND_OUTPUT, 'w') as f:
        f.write("%s %s %s" % (status, timestamp, msg))


class Command(BaseCommand):
    """
    Usage: python manage.py cms update_transcripts --settings=aws edX/Open_DemoX/edx_demo_course

    Args:
        course_id: edX/Open_DemoX/edx_demo_course
    """
    help = """Usage: update_transcripts [<course_id>]"""

    @handle_exception
    def handle(self, *args, **options):
        start_time = time.localtime()
        log.info("Command update_transcripts started at %s." % time.strftime('%Y-%m-%d %H:%M:%S', start_time))

        if len(args) > 1:
            raise CommandError("update_transcripts requires one or no arguments: |<course_id>|")

        # Check args: course_id
        course_id = args[0] if len(args) > 0 else None
        if course_id:
            try:
                Location.parse_course_id(course_id)
            except ValueError:
                raise CommandError("The course_id is not of the right format. It should be like 'org/course/name'")
        print "course_id=%s" % course_id

        # S3 store
        try:
            store = TranscriptS3Store()
        except Exception:
            raise CommandError("Could not establish a connection to S3 for transcripts backup. Check your credentials.")

        # Result
        output = SimpleTable()
        output.set_header(('Course ID', 'YouTube ID', 'Video Display Name', 'Status', 'Note'))

        # Find courses
        tag = 'i4x'
        if course_id:
            course_dict = Location.parse_course_id(course_id)
            org = course_dict['org']
            course = course_dict['course']
            name = course_dict['name']
            course_items = modulestore().get_items(Location(tag, org, course, 'course', name))
            if course_items:
                print "The specified course actually exists."
            else:
                raise CommandError("The specified course does not exist.")
        else:
            course_items = modulestore().get_courses()
            print "%s course(s) found." % len(course_items)

        for course_item in course_items:
            print "Now processing [%s] ............................................................" % course_item.id
            # Note: Use only active courses
            if course_item.has_ended():
                log.info("Skip processing course(%s) because the course has already ended." % course_item.id)
                output.add_row((course_item.id, "", "", "Skipped", "This course has already ended"))
                continue
            # Find video items
            video_items = modulestore().get_items(Location(course_item.location.tag, course_item.location.org, course_item.location.course, category='video'))
            print "%s video item(s) found." % len(video_items)
            for video_item in video_items:
                print "video_item.id=%s" % video_item.id
                youtube_id = video_item.youtube_id_1_0
                print "youtube_id=%s" % youtube_id

                # Get transcript from YouTube
                try:
                    youtube_transcript = YoutubeTranscript(video_item.location, youtube_id)
                except Exception as e:
                    log.warn(str(e))
                    output.add_row((course_item.id, youtube_id, video_item.display_name, "Failed", "Can't receive transcripts from YouTube"))
                    continue
                # Get transcript from local assets
                filename = youtube_transcript.get_filename()
                current_transcript_data = get_data_from_local(course_item.location, filename)

                # Note: Update local assets only when anything changed on YouTube
                if current_transcript_data is None or youtube_transcript.subs != json.loads(current_transcript_data):
                    # Upload YouTube transcript to local assets
                    youtube_transcript.upload_to_local()
                    # Store current transcript to S3 for backup
                    if current_transcript_data is not None:
                        backup_filename = subs_backup_filename(filename, start_time)
                        print "backup_filename=%s" % backup_filename
                        try:
                            store.save(course_item.id, current_transcript_data, backup_filename)
                        except Exception as e:
                            log.warn(
                                "Transcript for video(%s) was uploaded to the course(%s) successfully, but failed to store the backup file to S3."
                                % (youtube_id, course_item.id))
                            output.add_row((course_item.id, youtube_id, video_item.display_name, "Success", "WARN: Failed to store backup file to S3"))
                            continue
                    log.info(
                        "Transcript for video(%s) was uploaded to the course(%s) successfully!!"
                        % (youtube_id, course_item.id))
                    output.add_row((course_item.id, youtube_id, video_item.display_name, "Success", ""))
                else:
                    log.info(
                        "Skip uploading transcript for video(%s) because there is no difference between local assets and YouTube."
                        % youtube_id)
                    output.add_row((course_item.id, youtube_id, video_item.display_name, "Skipped", "No changes on YouTube"))

        end_time = time.localtime()
        log.info("Command update_transcripts ended at %s." % time.strftime('%Y-%m-%d %H:%M:%S', end_time))
        # Print result
        log.info(
            "\nResult:\n  started at : %s\n  ended at   : %s\n%s"
            % (time.strftime('%Y-%m-%d %H:%M:%S', start_time), time.strftime('%Y-%m-%d %H:%M:%S', end_time), '\n'.join(output.get_table())))


class YoutubeTranscript(object):
    """
    Transcript for YouTube
    """
    def __init__(self, old_location, youtube_id):
        self.old_location = old_location
        self.youtube_id = youtube_id
        self.lang = None

        # Download transcripts from YouTube
        # Note: cribbed from common/lib/xmodule/xmodule/video_module/transcripts_utils.py download_youtube_subs()
        self.settings = copy.deepcopy(settings)
        for lang in ['ja', 'en']:
            self.settings.YOUTUBE['TEXT_API']['params']['lang'] = lang
            try:
                self.subs = transcripts_utils.get_transcripts_from_youtube(self.youtube_id, self.settings, ModuleI18nService())
                self.lang = lang
                break
            except GetTranscriptsFromYouTubeException:
                continue
        else:
            raise Exception("Can't receive transcripts from Youtube for %s." % self.youtube_id)

    def get_filename(self):
        return subs_filename(self.youtube_id, self.lang)

    # Note: cribbed from cms/djangoapps/contentstore/views/assets.py _upload_asset()
    def upload_to_local(self):
        filename = self.get_filename()
        content_loc = StaticContent.compute_location(self.old_location.org, self.old_location.course, filename)
        mime_type = 'application/json'
        # Note: cribbed from common/lib/xmodule/xmodule/video_module/transcripts_utils.py save_subs_to_store()
        content = StaticContent(content_loc, filename, mime_type, json.dumps(self.subs, indent=2))
        contentstore().save(content)


# Note: modified from common/lib/xmodule/xmodule/video_module/transcripts_utils.py subs_filename()
def subs_filename(subs_id, lang='en'):
    """
    Generate proper filename for storage.
    """
    # TODO
    #if lang == 'en':
    #    return u'subs_{0}.srt.sjson'.format(subs_id)
    #else:
    #    return u'{0}_subs_{1}.srt.sjson'.format(lang, subs_id)
    return u'subs_{0}.srt.sjson'.format(subs_id)


def subs_backup_filename(filename, t):
    return u'{0}.{1}'.format(filename, time.strftime('%y%m%d%H%M%S', t))


def get_data_from_local(old_location, filename):
    """
    Get transcripts from local assets.

    Return None, if no such file exist on local assets
    """
    content_loc = StaticContent.compute_location(old_location.org, old_location.course, filename)
    try:
        data = contentstore().find(content_loc).data
    except NotFoundError:
        return
    return data


class TranscriptS3Store(object):
    """
    S3 store for transcripts
    """
    def __init__(self):
        self.bucket_name = settings.TRANSCRIPTS_BACKUP_BUCKET_NAME
        self.location = S3Location.APNortheast
        self.conn = self._connect()

    def _connect(self):
        return S3Connection(
            settings.AWS_ACCESS_KEY_ID,
            settings.AWS_SECRET_ACCESS_KEY)

    def save(self, course_id, data, filename):
        try:
            bucket = self.conn.get_bucket(self.bucket_name)
        except:
            raise
        try:
            s3key = Key(bucket)
            s3key.key = "{dir}/{cid}/{filename}".format(
                dir=settings.TRANSCRIPTS_BACKUP_DIR, cid=course_id, filename=filename)
            s3key.set_contents_from_string(data)
        except:
            raise
        finally:
            s3key.close()


class SimpleTable(object):
    """
    SimpleTable
    """
    def __init__(self, header=None, rows=None):
        self.header = header or ()
        self.rows = rows or []

    def set_header(self, header):
        self.header = header

    def add_row(self, row):
        self.rows.append(row)

    def _calc_maxes(self):
        array = [self.header] + self.rows
        return [max(self._unicode_width(s) for s in ss) for ss in izip_longest(*array, fillvalue='')]

    def _unicode_width(self, s, width={'F': 2, 'H': 1, 'W': 2, 'Na': 1, 'A': 2, 'N': 1}):
        s = unicode(s)
        return sum(width[east_asian_width(c)] for c in s)

    def _get_printable_row(self, row):
        maxes = self._calc_maxes()
        return '| ' + ' | '.join([unicode(r) + ' ' * (m - self._unicode_width(r)) for r, m in izip_longest(row, maxes, fillvalue='')]) + ' |'

    def _get_printable_header(self):
        return self._get_printable_row(self.header)

    def _get_printable_border(self):
        maxes = self._calc_maxes()
        return '+-' + '-+-'.join(['-' * m for m in maxes]) + '-+'

    def get_table(self):
        lines = []
        if self.header:
            lines.append(self._get_printable_border())
            lines.append(self._get_printable_header())
        lines.append(self._get_printable_border())
        for row in self.rows:
            lines.append(self._get_printable_row(row))
        lines.append(self._get_printable_border())
        return lines