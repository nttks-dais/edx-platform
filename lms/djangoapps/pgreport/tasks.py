"""
   Task that update ProgressModules table.
"""
from djcelery import celery
from pgreport.views import update_pgreport_table, create_pgreport_csv
from pgreport.views import UserDoesNotExists
from xmodule.modulestore.django import modulestore
from xmodule.modulestore import Location
import logging


log = logging.getLogger("update_tables_task")


@celery.task
def update_table_task(course_id):
    """Update progress_modules."""
    check_course_id(course_id)
    try: 
        update_pgreport_table(course_id)
    except UserDoesNotExists as e:
        return "%s (%s)" % (e, course_id)

    return "Update coplete!!! (%s)" % (course_id)


@celery.task
def create_report_task(course_id):
    """Create progress report."""
    check_course_id(course_id)
    try: 
        create_pgreport_csv(course_id)
    except UserDoesNotExists as e:
        return "%s (%s)" % (e, course_id)

    return "Create coplete!!! (%s)" % (course_id)


@celery.task
def update_table_task_for_active_course(course_id=None):
    """Update progress_modules table for active course."""
    task = ProgressReportTask(update_table_task)
    task.send_tasks(course_id)


def check_course_id(course_id):
    """Check course_id."""
    match = Location.COURSE_ID_RE.match(course_id)
    if match is None:
        raise ProgressreportException(
            "{} is not of form ORG/COURSE/NAME".format(course_id)
        )


class ProgressreportException(Exception):
    pass


class ProgressReportTask(object):
    """Task class for progress report."""

    def __init__(self, func):
        """Initialize"""
        self.task_func = func
        self.modulestore_name = 'default'
        if not hasattr(func, 'apply_async') or not hasattr(func, 'AsyncResult'):
            raise ProgressreportException("Funcion is not celery task.")

    def send_task(self, course_id):
        """Send task."""
        result = self.task_func.apply_async(
            args=(course_id,), expires=23.5*60*60, retry=False
        )
        print "Send task (task_id: %s)" % (result.id)

    def send_tasks(self, course_id):
        """Send task for all of active courses."""
        course_ids = []
        store = modulestore(self.modulestore_name)

        if course_id is None:
            for course in store.get_courses():
                if course.has_started() and not course.has_ended():
                    course_ids.append(course.location.course_id)
        else:
            check_course_id(course_id)
            course = store.get_course(course_id)

            if course is None:
                raise ProgressreportException("Course is not found (%s)" % (course_id))

            course_ids.append(course_id)
    
        for course_id in course_ids:
            result = self.task_func.apply_async(
                args=(course_id,), expires=23.5*60*60, retry=False
            )
            print "Send task (task_id: %s)" % (result.id)

    def show_task_status(self, task_id):
        """Show current state of task."""
        result = self.task_func.AsyncResult(task_id)
        if result.state == "PENDING":
            print "Task not found or PENDING state"
        else:
            print "Curent State: %s, %s" % (result.state, result.info)

    def show_task_list(self):
        """A view that returns active tasks"""
        stats = celery.control.inspect()
        print "*** Active queues ***"

        for queue, states in stats.active().items():
            if states:
                print "%s: [" % (queue)

                for state in states:
                    task_args = state['args']
                    task_id = state['id']
                    task_name = state['name']
                    worker_pid = state['worker_pid']
                    print " * Task id: %s, Task args: %s," % (
                        task_id, task_args),
                    print " Task name: %s, Worker pid: %s" % (
                        task_name, worker_pid)

                print "]"
            else:
                print "%s: []" % (queue)

    def revoke_task(self, task_id):
        """Send revoke signal to all workers."""
        result = self.task_func.AsyncResult(task_id)
        result.revoke(terminate=True)
