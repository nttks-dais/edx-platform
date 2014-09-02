"""
   Django management command to update ProgressModules table.
"""
from optparse import make_option
from django.core.management.base import BaseCommand, CommandError
from pgreport.tasks import update_table_task, ProgressReportTask, check_course_id


class Command(BaseCommand):
    args = "<update or list or status or revoke>"
    help = """Push task that update progress_modules table\n  update_table_task update: Update progress_module table.\n  update_table_task list: List active tasks.\n  update_table_task status -t [task_id]: Show task status.\n  update_table_task revoke -t [task_id]: Revoke task."""""
    option_list = BaseCommand.option_list + (
        make_option(
            '-c', '--course-id',
            action="store",
            default=None,
            dest='course_id',
            help='course_id that update progress_module table. if this option is not specified, all of the courses that not ended is updated.'
        ),
        make_option(
            '-t', '--task-id',
            action="store",
            default=None,
            dest='task_id',
            help='task_id that show status or revoke task'
        ),
    )

    def handle(self, *args, **options):
        """Handle command options."""
        course_id = options['course_id']
        task_id = options['task_id']

        if course_id is not None:
            check_course_id(course_id)

        if len(args) != 1:
            raise CommandError('Required subcommand, update, list, status or revoke.')
        command = args[0]
        task = ProgressReportTask(update_table_task)

        if command == "status":
            if task_id is None:
                raise CommandError('"status" subcommand required task_id.')
            task.show_task_status(task_id)
        elif command == "list":
            task.show_task_list()
        elif command == "revoke":
            if task_id is None:
                raise CommandError('"revoke" subcommand required task_id.')
            task.revoke_task(task_id)
        elif command == "update":
            task.send_tasks(course_id)
        else:
            raise CommandError('Invalid subcommand.')
