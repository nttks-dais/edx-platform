"""
progress report views
"""
from django.contrib.auth.models import User
from django.core.cache import cache
from django.conf import settings
from django.test.client import RequestFactory
from courseware import grades
from courseware.courses import get_course
from student.models import UserStanding
from instructor.utils import get_module_for_student
import numpy as np
import unicodecsv as csv
import logging

from .models import ProgressModules
from xmodule.modulestore import Location
from xmodule.contentstore.content import StaticContent
from xmodule.contentstore.django import contentstore
from gridfs.errors import GridFSError 
from xmodule.exceptions import NotFoundError
from django.db import DatabaseError


log = logging.getLogger("progress_report")
OPERATION = {
    "all": ("summary", "modules", "students"),
    "tables": ("summary", "modules"),
    "summary": ("summary"),
    "modules": ("modules"),
    "students": ("students"),
}
ROUND=3


class ProgressReportException(Exception):
    pass


class InvalidCommand(ProgressReportException):
    pass


class UserDoesNotExists(ProgressReportException):
    pass


class ProgressReport(object):
    """Progress report class"""

    def __init__(self, course_id, debug=False):
        self.course_id = course_id
        self.debug = debug
        self.module_summary = {}
        self.students_summary = {}
        self.module_statistics = {}
	self.course = get_course(self.course_id)
        self.request = self._create_request()
        self.enroll_count, self.active_count, self.students = self.get_active_students(
            self.course_id)
        self.location_list = {}
        self.location_parent = []
        self._get_children_rec(self.course)
        self.courseware_summary = {
            "enrollments": self.enroll_count,
            "active_students": self.active_count,
            "module_tree": self.location_parent
        }

    @staticmethod
    def get_active_students(course_id):
        """"""
        enrollments = User.objects.filter(
            courseenrollment__course_id__exact=course_id)
        active_students = enrollments.exclude(
            standing__account_status__exact=UserStanding.ACCOUNT_DISABLED)

        if not active_students:
            log.error("Enrolled active user does not exists.")
            raise UserDoesNotExists("Enrolled active user does not exists.")

        return (enrollments.count(), active_students.count(), active_students)

    def _create_request(self):
        """"""
        factory = RequestFactory()
        request = factory.get('/')
        request.session = {}
        return request

    def _calc_statistics(self):
        module_summary_data = {}
        for key, stat_list in self.module_statistics.items():
            stat = np.array(stat_list)
            module_summary_data[key] = {}
            module_summary_data[key]["mean"] = round(np.mean(stat), ROUND)
            module_summary_data[key]["median"] = round(np.median(stat), ROUND)
            module_summary_data[key]["variance"] = round(np.var(stat), ROUND)
            module_summary_data[key]["standard_deviation"] = round(np.std(stat), ROUND)

        return module_summary_data

    def _get_correctmap(self, module):
        """
        if problem is not choosen, corrects is empty.
        """
        corrects = {}
        for key in module.correct_map.keys():
            if module.correct_map[key].get("correctness") == "correct":
                corrects.update({key: 1})
            else:
                corrects.update({key: 0})

        return corrects

    def _get_student_answers(self, module):
        """
        if problem is not choosen, corrects is empty.
        """
        student_answers = {}
        for key, answers in module.student_answers.items():
            if isinstance(answers, list):
                student_answers[key] = {}
                for answer in answers:
                    student_answers[key][answer] = 1
            else:
                student_answers[key] = {answers: 1}

        return student_answers

    def _get_module_data(self, module):
        corrects = self._get_correctmap(module)
        student_answers = self._get_student_answers(module)
        return {
            "display_name": module.display_name,
            "type": module.category,
            "start": module.start,
            "due": module.due,
            "weight": module.weight,
            "score/total": module.get_progress(),
            "correct_map": corrects,
            "student_answers": student_answers,
        }

    def _increment_student_answers(self, name, answers, unit_id):
        """"""
        for key, value in answers.items():
            if self.module_summary[name]["student_answers"].has_key(unit_id):
                if self.module_summary[name]["student_answers"][unit_id].has_key(key):
                    self.module_summary[name]["student_answers"][unit_id][key] += value
                else:
                    self.module_summary[name]["student_answers"][unit_id][key] =  value
            else:
                self.module_summary[name]["student_answers"][unit_id] = {key: value}

    def _increment_student_correctmap(self, name, value, unit_id):
        """"""
        if self.module_summary[name]["correct_map"].has_key(unit_id):
            self.module_summary[name]["correct_map"][unit_id] += value
        else:
            self.module_summary[name]["correct_map"][unit_id] = value

    def collect_module_summary(self, module):
        """"""
        name = module.location
        module_data = {}
        module_data = self._get_module_data(module)
        corrects = module_data["correct_map"]
        student_answers = module_data["student_answers"]
        score, total = module.get_progress().frac()

        if not self.module_statistics.has_key(name):
            self.module_statistics[name] = []
        self.module_statistics[name].append(float(score))

        if self.module_summary.has_key(name):
            for unit_id, value in corrects.items():
                self._increment_student_correctmap(name, value, unit_id)
            for unit_id, answers in student_answers.items():
                if isinstance(answers, list):
                    for answer in answers:
                        self._increment_student_answers(name, answer, unit_id)
                else:
                    self._increment_student_answers(name, answers, unit_id)
        else:
            self.module_summary[name] = {
                "max_score": 0.0,
                "total_score": 0.0,
                "count": 0,
                "submit_count": 0,
                "correct_count": 0,
                "nonsubmit_count": 0,
            }
            self.module_summary[name].update(module_data)

        self.module_summary[name]["max_score"] += float(score)
        self.module_summary[name]["total_score"] += float(total)
        self.module_summary[name]["count"] += 1

        if module.is_submitted():
            self.module_summary[name]["submit_count"] += 1
        else:
            self.module_summary[name]["nonsubmit_count"] += 1
        if module.is_correct():
            self.module_summary[name]["correct_count"] += 1
    
    def collect_student_summary(self, module, student, grade):
        name = module.location
        module_data = {}
        module_data[name] = self._get_module_data(module)

        if self.students_summary.has_key(student.username):
            module_data[name].update({
                "last_login": student.last_login.strftime("%Y/%m/%d %H:%M:%S %Z"),
                "grade": grade["grade"],
                "percent": grade["percent"],
                "location": name,
            })
            rows = []
            for header in self.students_summary["students:summary:header"]:
                rows.append(module_data[name][header])

            self.students_summary[student.username].append(rows)
        else:
            self.students_summary["students:summary:header"] = [
                "location", "last_login", "grade", "percent"]
            self.students_summary[student.username] = []
            self.students_summary[student.username].append(
                [name, student.last_login.strftime("%Y/%m/%d %H:%M:%S %Z"),
                grade["grade"], grade["percent"]])
            for key in module_data[name].keys():
                self.students_summary["students:summary:header"].append(key)
                self.students_summary[student.username][0].append(module_data[name][key])

    def _get_children_rec(self, course, parent=None):
        """"""
        for child in course.get_children():
            locations = self.location_list[child.category] if self.location_list.has_key(
                child.category) else []
            locations.append(child.location)
            self.location_list[child.category] = locations

            if child.category in ("sequential", "chapter", "vertical"):
                if parent:
                    parent.append(child.display_name)
                else:
                    parent = [child.display_name]
            else:
                self.location_parent.append({child.location: list(parent)})

            if child.has_children:
                self._get_children_rec(child, parent)
                parent.pop()

    def get_raw(self, command="all"):
        """"""
        if not OPERATION.has_key(command):
            log.error('Invalid command: {}'.format(command))
            raise InvalidCommand("Invalid command: {}".format(command))

        if command == "summary":
           return self.courseware_summary 

        self.courseware_summary["graded_students"] = 0
        for student in self.students.iterator():
            self.request.user = student
            log.debug(" * Active user: {}".format(student))
            grade = grades.grade(student, self.request, self.course)

            if grade["grade"] is not None:
                self.courseware_summary["graded_students"] += 1 

            for category in self.location_list.keys():
                if category not in ["problem"]:
                    continue

                for loc in self.location_list[category]:
                    log.debug(" * Active location: {}".format(loc))
                    module = get_module_for_student(student, self.course, loc)

                    if module is None:
                        log.warn(" * WARNING: No state found: %s" % (student))
                        continue

                    if "modules" in OPERATION[command]:
                        self.collect_module_summary(module)

                    if "students" in OPERATION[command]:
                        self.collect_student_summary(module, student, grade)

        cache.set('progress_summary', self.courseware_summary["graded_students"],
            timeout=24*60*60)

        if self.module_summary:
            statistics = self._calc_statistics()
            for key in statistics.keys():
                self.module_summary[key].update(statistics[key])

        if command == "modules":
            return self.module_summary
        elif command == "students":
            return self.students_summary

        return self.courseware_summary, self.students_summary, self.module_summary


def get_pgreport_csv(course_id):
    """"""
    tag = "i4x"
    org, course, category = course_id.split('/')
    loc = Location(tag, org, course, category="pgreport", name="progress_students.csv")
    store = contentstore()

    try:
        content = store.find(loc, throw_on_not_found=True, as_stream=True)

        for csv_data in content.stream_data():
            print csv_data
    except NotFoundError as e:
        log.warn(" * WARNING: Csv does not exists: {}".format(e))
        raise


def create_pgreport_csv(course_id):
    """"""
    progress = ProgressReport(course_id)
    students_summary = progress.get_raw(command="students")
    tag = "i4x"
    org, course, category = course_id.split('/')
    loc = Location(tag, org, course, category="pgreport", name="progress_students.csv")
    content = StaticContent(loc, "students-progress", "text/csv", "dummy-data")
    content_id = content.get_id()
    store = contentstore()
    store.delete(content_id)

    try:
        with store.fs.new_file(
            _id=content_id, filename=content.get_url_path(),
            content_type=content.content_type, displayname=content.name,
            thumbnail_location=content.thumbnail_location,
            import_path=content.import_path,
            locked=getattr(content, 'locked', False)) as fp:

            writer = csv.writer(fp, encoding='utf-8')
            writer.writerow(["username"] + students_summary.pop("students:summary:header"))

            for username, rows in students_summary.items():
                for row in rows:
                    writer.writerow([username] + row)

    except GridFSError as e:
        store.delete(content_id)
        log.error(" * GridFS Error: {}".format(e))
        raise


def delete_pgreport_csv(course_id):
    """"""
    tag = "i4x"
    org, course, category = course_id.split('/')
    loc = Location(tag, org, course, category="pgreport", name="progress_students.csv")
    content = StaticContent(loc, "students-progress", "text/csv", "dummy-data")
    store = contentstore()
    store.delete(content.get_id())


def get_pgreport_table(course_id):
    """"""
    progress = ProgressReport(course_id)
    summary = progress.get_raw(command="summary")
    modules_dict = ProgressModules.objects.filter(course_id=course_id).values()
    modules = {}

    for module in modules_dict:
        loc = module.pop("location")
        modules[str(loc)] = module

    return summary, modules


def update_pgreport_table(course_id):
    """"""
    progress = ProgressReport(course_id)
    modules = progress.get_raw(command="modules")

    for loc, params in modules.items():
        try:
            progress_entry = ProgressModules(
                course_id = course_id,
                location = loc,
                display_name = params["display_name"],
                count = params["count"],
                max_score = params["max_score"],
                total_score = params["total_score"],
                submit_count = params["submit_count"],
                weight = params["weight"],
                start = params["start"],
                due = params["due"],
                correct_map = params["correct_map"],
                student_answers = params["student_answers"],
                mean = params["mean"],
                median = params["median"],
                variance = params["variance"],
                standard_deviation = params["standard_deviation"])
            progress_entry.save()
        except DatabaseError as e:
            log.error(" * Database Error: {}".format(e))
            raise
