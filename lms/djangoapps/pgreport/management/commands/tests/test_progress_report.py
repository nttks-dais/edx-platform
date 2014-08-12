from django.test import TestCase
from django.core.management.base import CommandError
from mock import patch
from pgreport.management.commands import progress_report as pr


class ProgressReportCommandTestCase(TestCase):

    def setUp(self):
        self.args = ["org/num/run"]
        self.options1 = {'update_table': True}
        self.options2 = {'update_table': False}

    def tearDown(self):
        pass

    @patch('pgreport.management.commands.progress_report.update_pgreport_table')
    @patch('pgreport.management.commands.progress_report.get_pgreport_csv')
    def test_handle(self, get_mock, update_mock):
        pr.Command().handle(*self.args, **self.options1)
        update_mock.assert_called_once_with('org/num/run')
        self.assertEquals(get_mock.mock_calls, [])

        get_mock.reset_mock(), update_mock.reset_mock()

        pr.Command().handle(*self.args, **self.options2)
        get_mock.assert_called_once_with('org/num/run')
        self.assertEquals(update_mock.mock_calls, [])

        with self.assertRaises(CommandError) as ce_mock:
            pr.Command().handle(*[], **self.options1)
