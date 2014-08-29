"""
Unit tests for progress report background tasks.
"""
from django.test import TestCase
from mock import Mock, MagicMock, patch, ANY
from contextlib import nested
from django.test.utils import override_settings
from courseware.tests.modulestore_config import TEST_DATA_MIXED_MODULESTORE
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory
from pgreport.tasks import (
    update_table_task, create_report_task, update_table_task_for_active_course,
    check_course_id, ProgressreportException, ProgressReportTask
)
from pgreport.views import UserDoesNotExists
import datetime
from pytz import UTC
import StringIO


@override_settings(MODULESTORE=TEST_DATA_MIXED_MODULESTORE)
class ProgressReportTaskTestCase(ModuleStoreTestCase):
    """Test Progress Report Task"""
    COURSE_NAME = "test_task"

    def setUp(self):
        self.course1 = CourseFactory.create(display_name=self.COURSE_NAME + "1")
        self.course2 = CourseFactory.create(display_name=self.COURSE_NAME + "2")
        self.course3 = CourseFactory.create(display_name=self.COURSE_NAME + "3")
        self.course4 = CourseFactory.create(display_name=self.COURSE_NAME + "4")
        self.course1.start  = datetime.datetime(100, 12, 31, 0, 0, tzinfo=UTC)
        self.course1.end = datetime.datetime(2999, 12, 31, 0, 0, tzinfo=UTC)
        self.course2.start  = datetime.datetime(100, 12, 31, 0, 0, tzinfo=UTC)
        self.course2.end = None
        self.course3.start  = datetime.datetime(100, 12, 31, 0, 0, tzinfo=UTC)
        self.course3.end = datetime.datetime(1000, 12, 31, 0, 0, tzinfo=UTC)
        self.course4.start  = datetime.datetime(2030, 1, 1, tzinfo=UTC)
        self.course4.end = None
        self.courses = [self.course1, self.course2, self.course3, self.course4]    
        self.func_mock = MagicMock()

    def tearDown(self):
        pass

    @patch('pgreport.tasks.check_course_id')
    @patch('pgreport.tasks.update_pgreport_table')
    def test_update_table_task(self, update_mock, check_mock):
        result = update_table_task(self.course1.id)
        update_mock.assert_called_once_with(self.course1.id)
        check_mock.assert_called_once_with(self.course1.id)
        self.assertEquals(result, "Update coplete!!! (%s)" % self.course1.id)

        msg = "Test update_table_task!"
        update_mock.side_effect = UserDoesNotExists(msg)
        result = update_table_task(self.course1.id)
        self.assertEquals(result, "%s (%s)" % (msg, self.course1.id))

    @patch('pgreport.tasks.check_course_id')
    @patch('pgreport.tasks.create_pgreport_csv')
    def test_create_report_task(self, create_mock, check_mock):
        result = create_report_task(self.course1.id)
        create_mock.assert_called_once_with(self.course1.id)
        check_mock.assert_called_once_with(self.course1.id)
        self.assertEquals(result, "Create coplete!!! (%s)" % self.course1.id)

        msg = "Test create_report_task!"
        create_mock.side_effect = UserDoesNotExists(msg)
        result = create_report_task(self.course1.id)
        self.assertEquals(result, "%s (%s)" % (msg, self.course1.id))

    def test_update_table_task_for_active_course(self):
        task_mock = MagicMock()
        with patch('pgreport.tasks.ProgressReportTask',
             return_value=task_mock) as prt_mock:

            update_table_task_for_active_course(self.course1.id)
            prt_mock.assert_called_once_with(update_table_task)
            task_mock.send_tasks.assert_called_once_with(self.course1.id)

    @patch('pgreport.tasks.Location')
    def test_check_course_id(self, loc_mock):
        check_course_id(self.course1.id)
        loc_mock.COURSE_ID_RE.match.assert_called_once_with(self.course1.id)
        
        loc_mock.COURSE_ID_RE.match.return_value = None
        msg = "^{} is not of form ORG/COURSE/NAME$".format("wrong-course-id")
        with self.assertRaisesRegexp(ProgressreportException, msg):
            check_course_id("wrong-course-id")

    def test_init(self):
        task = ProgressReportTask(self.func_mock)

        fake_func = lambda x: x
        msg = "^Funcion is not celery task.$"
        with self.assertRaisesRegexp(ProgressreportException, msg):
            task = ProgressReportTask(fake_func)
        
    def test_send_task(self):
        task = ProgressReportTask(self.func_mock)
        with patch('sys.stdout', new_callable=StringIO.StringIO) as std_mock:
            task.send_task(self.course1.id)

        self.func_mock.apply_async.assert_called_once_with(
            args=(self.course1.id,), expires=23.5*60*60, retry=False
        )
        self.assertEquals(std_mock.getvalue(),
            "Send task (task_id: %s)\n" % (self.func_mock.apply_async().id)
        )

    @patch('pgreport.tasks.modulestore') 
    def test_send_tasks(self, module_mock):
        task = ProgressReportTask(self.func_mock)
        with patch('sys.stdout', new_callable=StringIO.StringIO) as std_mock:
            task.send_tasks(self.course1.id)

        self.func_mock.apply_async.assert_called_once_with(
            args=(self.course1.id,), expires=23.5*60*60, retry=False
        )
        self.assertEquals(std_mock.getvalue(),
            "Send task (task_id: %s)\n" % (self.func_mock.apply_async().id)
        )

        msg = "^Course is not found \({}\)".format(self.course1.id)
        with self.assertRaisesRegexp(ProgressreportException, msg):
            store_mock = MagicMock()
            store_mock.get_course.return_value = None
            module_mock.return_value = store_mock
            task.send_tasks(self.course1.id)

        with patch('sys.stdout', new_callable=StringIO.StringIO) as std_mock:
            store_mock = MagicMock()
            store_mock.get_courses.return_value = self.courses
            module_mock.return_value = store_mock
            task.send_tasks(None)

        # self.course1 and self.course2 are opend and not closed.
        self.assertEquals(std_mock.getvalue(),
            "Send task (task_id: %s)\n" % (self.func_mock.apply_async().id) +
            "Send task (task_id: %s)\n" % (self.func_mock.apply_async().id)
        )

    def test_show_task_status(self):
        task = ProgressReportTask(self.func_mock)
        with patch('sys.stdout', new_callable=StringIO.StringIO) as std_mock:
            result_mock = MagicMock()
            result_mock.state = "PENDING"
            self.func_mock.AsyncResult.return_value = result_mock 
            task.show_task_status("task_id")

        self.func_mock.AsyncResult.assert_called_once_with("task_id")
        self.assertEquals(std_mock.getvalue(),
            "Task not found or PENDING state\n"
        )

        with patch('sys.stdout', new_callable=StringIO.StringIO) as std_mock:
            result_mock = MagicMock()
            result_mock.state = "SUCCESS"
            self.func_mock.AsyncResult.return_value = result_mock 
            task.show_task_status("task_id")

        self.assertEquals(std_mock.getvalue(),
            "Curent State: {}, {}\n".format(result_mock.state, result_mock.info)
        )

    def test_show_task_list(self):
        task = ProgressReportTask(self.func_mock)
        with nested(
            patch('sys.stdout', new_callable=StringIO.StringIO),
            patch('pgreport.tasks.celery')
        ) as (std_mock, cel_mock):
            stat_mock = MagicMock()
            stat_mock.active.return_value = {"queue": None}
            cel_mock.control.inspect.return_value = stat_mock
            task.show_task_list()    

        cel_mock.control.inspect.assert_called_once_with()
        self.assertEquals(std_mock.getvalue(),
            "*** Active queues ***\n{}: []\n".format("queue")
        )

        status = {
            "args": "argment",
            "id": "task_id",
            "name": "task_name",
            "worker_pid": "worker_pid",
        }
        with nested(
            patch('sys.stdout', new_callable=StringIO.StringIO),
            patch('pgreport.tasks.celery')
        ) as (std_mock, cel_mock):
            stat_mock = MagicMock()
            stat_mock.active.return_value = {"queue": [status]}
            cel_mock.control.inspect.return_value = stat_mock
            task.show_task_list()    

        self.assertEquals(std_mock.getvalue(),
            '*** Active queues ***\nqueue: [\n * Task id: {id}, Task args: {args},  Task name: {name}, Worker pid: {worker_pid}\n]\n'.format(**status)
        )

    def test_revoke_task(self):
        task = ProgressReportTask(self.func_mock)
        task.revoke_task("task_id")    
        self.func_mock.AsyncResult.assert_called_once_with("task_id")
        self.func_mock.AsyncResult().revoke.assert_called_once_with(terminate=True)
