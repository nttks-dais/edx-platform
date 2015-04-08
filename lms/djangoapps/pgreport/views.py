"""
Function to grasp the progress of the course.
"""
import logging
import json
from django.views.decorators import csrf
from django.views.decorators.http import require_GET
from django.utils.translation import ugettext as _
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from student.models import UserStanding
from courseware.models import StudentModule
from courseware.courses import get_course
from util.json_request import JsonResponse, JsonResponseBadRequest
from opaque_keys.edx.keys import CourseKey, AssetKey

from xmodule.contentstore.content import StaticContent
from xmodule.contentstore.django import contentstore
from xmodule.exceptions import NotFoundError


from django_future.csrf import ensure_csrf_cookie
from django.views.decorators.cache import cache_control
from instructor.views.tools import handle_dashboard_error
from instructor.views.api import require_level

#from opaque_keys import InvalidKeyError
#from xmodule.contentstore.content import StaticContent
#from xmodule.contentstore.django import contentstore
#from gridfs.errors import GridFSError
#from xmodule.exceptions import NotFoundError
#from django.core.cache import cache
#from django.db import DatabaseError

#import unicodecsv as csv
#import StringIO
#import gzip

"""
<div style="display:none">
    <input type="hidden" name="csrfmiddlewaretoken" value="$csrf_token"/>
</div>
"""

log = logging.getLogger("progress_report")


class ProgressReportException(Exception):
    pass


class UserDoesNotExists(ProgressReportException):
    pass


def get_coursekey(course_id):
    return CourseKey.from_string(course_id) if not isinstance(course_id, CourseKey) else course_id


class ProgressReport(object):
    """Progress report class."""

    def __init__(self, course_id):
        """Initialize."""
        self.course_id = get_coursekey(course_id)
        self.location_list = []

    def get_active_students(self):
        """Get active enrolled students."""
        enrollments = User.objects.filter(
            courseenrollment__course_id__exact=self.course_id)

        active_students = enrollments.filter(is_active=1).exclude(
            standing__account_status__exact=UserStanding.ACCOUNT_DISABLED)

        return (enrollments.count(), active_students.count())

    def get_course_structure(self):
        self.location_list = []
        course = get_course(self.course_id)
        self._get_children_module(course)

        return self.location_list

    def _get_children_module(self, course, parent=[]):
        for child in course.get_children():
            if child.category not in (
                "chapter", "sequential", "vertical", "problem", "openassessment"):
                continue

            module = {
                "usage_id": child.scope_ids.usage_id.to_deprecated_string(),
                "category": child.category,
                "display_name": child.display_name_with_default,
                "indent": len(parent),
                "module_size": 0,
                "has_children": child.has_children,
                "parent": list(parent),
            }

            self.location_list.append(module)

            if child.has_children:
                parent.append(child.scope_ids.usage_id.to_deprecated_string())
                self._get_children_module(child, parent) 
                parent.pop()
            else:
                self.location_list[-1]["module_size"] += 1

    def get_problem_data(self):
        problem_data = {}
        for module in StudentModule.all_submitted_problems_read_only(self.course_id):
            key = str(module.module_state_key)
            state = json.loads(module.state)

            if problem_data.has_key(key):
                current = problem_data[key]
                problem_data[key] = {
                    "counts": current["counts"] + 1,
                    "attempts": current["attempts"] + state.get("attempts"),
                    "correct_maps":  self._get_correctmap_data(
                        current["correct_maps"], state.get("correct_map")),
                    "student_answers": self._get_student_answers_data(
                        current["student_answers"], state.get("student_answers")),
                }
            else:
                problem_data[key] = {
                    "counts": 1,
                    "attempts": state.get("attempts"),
                    "correct_maps":  self._get_correctmap_data(
                        {}, state.get("correct_map")),
                    "student_answers": self._get_student_answers_data(
                        {}, state.get("student_answers")),
                }

        return problem_data

    def _get_correctmap_data(self, sum_correct_map, correct_map):
        for key in correct_map.keys():
            corrects_data = {}
            if correct_map[key].get("correctness") == "correct":
                corrects_data.update({key: 1})
            else:
                corrects_data.update({key: 0})

            if sum_correct_map.has_key(key):
                sum_correct_map[key] += corrects_data[key]
            else:
                sum_correct_map[key] = corrects_data[key]

        return sum_correct_map

    def _get_student_answers_data(self, sum_student_answers, student_answers):

        for key, answers in student_answers.items():
            answers_data = {}
            if isinstance(answers, list):
                answers_data[key] = {}
                for answer in answers:
                    answers_data[key][answer] = 1
            else:
                answers_data[key] = {answers: 1}

            if sum_student_answers.has_key(key):
                for answer, count in answers_data[key].items():
                    if sum_student_answers[key].has_key(answer):
                        sum_student_answers[key][answer] += answers_data[key][answer]
                    else:
                        sum_student_answers[key][answer] = answers_data[key][answer]
            else:
                sum_student_answers[key] = answers_data[key]

        return sum_student_answers

    def get_progress_list(self):
        structure = self.get_course_structure()
        problems = self.get_problem_data()
        result = list(structure)

        for idx in xrange(0, len(structure)):
            if structure[idx]["category"] != "problem": continue
            usage_id = structure[idx]["usage_id"] 

            try:
                i = 0
                for key, value in sorted(problems[usage_id]["student_answers"].items()):
                    module = structure[idx].copy()
                    module["module_id"] = key
                    module["student_answers"] = value
                    module["counts"] = problems[usage_id]["counts"]
                    module["attempts"] = problems[usage_id]["attempts"]
                    if problems[usage_id]["correct_maps"].has_key(key):
                        module["correct_counts"] = problems[usage_id]["correct_maps"][key]

                    result.insert(idx + i, module)
                    i += 1

            except KeyError as e:
                pass

        return result

    def handle_ajax(self, dispatch, data):
        """
        This is called by courseware.module_render, to handle an AJAX call.
        `data` is request.POST.
        """
        handlers = {}
        #_ = self.runtime.service(self, "i18n").ugettext

        if dispatch not in handlers:
            return 'Error: {} is not a known capa action'.format(dispatch)

        try:
            result = handlers[dispatch](data)
        except:
            pass

        return json.dumps(result, cls=ComplexEncoder)


@ensure_csrf_cookie
@handle_dashboard_error
@require_level('staff')
@require_GET
@cache_control(no_cache=True, no_store=True, must_revalidate=True)
def ajax_get_course_structure(request, course_id):
    progress = ProgressReport(course_id)
    json = progress.get_course_structure()
    return JsonResponse(json)


@ensure_csrf_cookie
@handle_dashboard_error
@require_level('staff')
@require_GET
@cache_control(no_cache=True, no_store=True, must_revalidate=True)
def ajax_get_problem_data(request, course_id):
    progress = ProgressReport(course_id)
    json = progress.get_problem_data()
    return JsonResponse(json)

@ensure_csrf_cookie
@handle_dashboard_error
@require_level('staff')
@require_GET
@cache_control(no_cache=True, no_store=True, must_revalidate=True)
def ajax_get_progress_list(request, course_id):
    progress = ProgressReport(course_id)
    json = progress.get_progress_list()
    return JsonResponse(json)

@ensure_csrf_cookie
@handle_dashboard_error
@require_level('staff')
@require_GET
@cache_control(no_cache=True, no_store=True, must_revalidate=True)
def ajax_get_pgreport_csv(request, course_id):
    course_key = SlashSeparatedCourseKey.from_deprecated_string(course_id)
    loc = StaticContent.compute_location(course_key, "progress_students.csv.gz")
    store = contentstore()
    try:
        content = store.find(loc, throw_on_not_found=True, as_stream=True)
    except NotFoundError as e:
        return HttpResponseForbidden(e)

    response = HttpResponse(content_type="application/x-gzip")
    response['Content-Disposition'] = 'attachment; filename={}'.format(content.name)
    for csv_data in content.stream_data():
        response.write(csv_data)

    return response


    """
    def _get_children_module(self, course, parent=[]):
        for child in course.get_children():
            if child.category not in (
                "chapter", "sequential", "vertical", "problem", "openassessment"):
                continue

            module = {
                "usage_id": child.scope_ids.usage_id.to_deprecated_string(),
                "category": child.category,
                "display_name": child.display_name_with_default,
                "children": []
            }

            if child.has_children:
                if parent:
                    parent["children"].append(module)
                else:
                    self.location_list.append(module)

                self._get_children_module(child, module)
            else:
                if parent:
                    parent["children"].append(module)
                else:
                    self.location_list.append(module)
    """
"""
def get_pgreport_csv(course_id):
    course_key = get_coursekey(course_id)
    location = StaticContent.compute_location(course_key, "progress_students.csv.gz")
    store = contentstore()

    try:
        gzipfile = StringIO.StringIO()
        content = store.find(location, throw_on_not_found=True, as_stream=True)
        for gzipdata in content.stream_data():
            gzipfile.write(gzipdata)

        gzipfile.seek(0)
        gzipcsv = gzip.GzipFile(fileobj=gzipfile, mode='rb')
        for csvrow in gzipcsv.readlines():
            print csvrow,
        gzipcsv.close()

    except NotFoundError as e:
        log.warn(" * Csv does not exists: {}".format(e))

    finally:
        gzipfile.close()


def create_pgreport_csv(course_id, update_state=None):
    course_key = get_coursekey(course_id)

    try:
        gzipfile = StringIO.StringIO()
        gzipcsv = gzip.GzipFile(filename="progress_students.csv.gz", mode='wb', fileobj=gzipfile)
        writer = csv.writer(gzipcsv, encoding='utf-8')
        progress = ProgressReport(course_key, update_state)

        for row in progress.yield_students_progress():
            writer.writerow(row)

    finally:
        gzipcsv.close()

    try:
        content_loc = StaticContent.compute_location(course_key, gzipcsv.name)
        content = StaticContent(
            loc=content_loc,
            name=gzipcsv.name,
            content_type="application/x-gzip",
            data=gzipfile.getvalue())
        contentstore().save(content)
        del_cached_content(content_loc)

    except GridFSError as e:
        store.delete(content_id)
        log.error(" * GridFS Error: {}".format(e))
        raise

    finally:
        gzipfile.close()


def delete_pgreport_csv(course_id):
    course_key = get_coursekey(course_id)
    location = StaticContent.compute_location(course_key, "progress_students.csv.gz")
    store = contentstore()
    content = store.find(location)
    store.delete(content.get_id())
"""
