import logging
from itertools import izip_longest
from unicodedata import east_asian_width

from django.core.management.base import BaseCommand, CommandError

from xmodule.modulestore.django import modulestore
from xmodule.modulestore import Location


log = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Usage: python manage.py cms check_draft --settings=aws edX/Open_DemoX/edx_demo_course

    Args:
        course_id: edX/Open_DemoX/edx_demo_course
    """
    help = """Usage: check_draft [<course_id>]"""

    def handle(self, *args, **options):
        if len(args) > 1:
            raise CommandError("check_draft requires one or no arguments: |<course_id>|")

        # Check args: course_id
        course_id = args[0] if len(args) > 0 else None
        if course_id:
            try:
                Location.parse_course_id(course_id)
            except ValueError:
                raise CommandError("The course_id is not of the right format. It should be like 'org/course/name'")

        # Result
        output = SimpleTable()
        output.set_header(('Course ID', 'Course Name', 'Chapter Name', 'Sequential Name', 'Vertical Name', 'Draft?'))

        # Find courses
        tag = 'i4x'
        if course_id:
            course_dict = Location.parse_course_id(course_id)
            org = course_dict['org']
            course = course_dict['course']
            name = course_dict['name']
            course_items = modulestore().get_items(Location(tag, org, course, 'course', name))
            if not course_items:
                raise CommandError("The specified course does not exist.")
        else:
            course_items = modulestore().get_courses()

        for course_item in course_items:
            # Note: Use only active courses
            if course_item.has_ended():
                continue
            # Find chapter items
            chapter_items = [modulestore().get_item(Location(item_id)) for item_id in course_item.children]
            chapter_items = sorted(chapter_items, key=lambda item: item.start)
            for chapter_item in chapter_items:
                # Find sequential items
                sequential_items = [modulestore().get_item(Location(item_id)) for item_id in chapter_item.children]
                sequential_items = sorted(sequential_items, key=lambda item: item.start)
                for sequential_item in sequential_items:
                    # Find vertical items
                    vertical_items = [modulestore().get_item(Location(item_id)) for item_id in sequential_item.children]
                    vertical_items = sorted(vertical_items, key=lambda item: item.start)
                    for vertical_item in vertical_items:
                        if vertical_item.is_draft:
                            output.add_row((course_item.id, course_item.display_name, chapter_item.display_name, sequential_item.display_name, vertical_item.display_name, vertical_item.is_draft))

        # Print result
        output.print_table()


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

    def print_table(self):
        lines = self.get_table()
        for line in lines:
            print(line)