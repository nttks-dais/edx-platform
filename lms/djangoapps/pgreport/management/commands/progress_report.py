"""
   Django management command to get report of progress as csv or
   update table of ProgressModules.
"""
from optparse import make_option
from django.core.management.base import BaseCommand, CommandError
from pgreport.views import update_pgreport_table, get_pgreport_csv


class Command(BaseCommand):
    args = "<course_id>"
    help = """Certificate command."""
    option_list = BaseCommand.option_list + (
        make_option(
            '-t', '--update-table',
            action="store_true",
            dest='update_table',
            default=False,
            help='Update progress_modules table.'
        ),
    )

    def handle(self, *args, **options):
        update = options['update_table']

        if len(args) != 1:
            raise CommandError("course_id not specified")
        course_id = args[0]
        if update:
            update_pgreport_table(course_id)
        else:
            get_pgreport_csv(course_id)
