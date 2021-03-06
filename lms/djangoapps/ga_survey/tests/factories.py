"""
Provides factories for survey models.
"""
from datetime import datetime
from pytz import UTC

import factory
from factory.django import DjangoModelFactory

from ga_survey.models import SurveySubmission
from opaque_keys.edx.locator import CourseLocator
from student.tests.factories import UserFactory

# Factories don't have __init__ methods, and are self documenting
# pylint: disable=W0232, C0111


class SurveySubmissionFactory(DjangoModelFactory):
    FACTORY_FOR = SurveySubmission

    course_id = CourseLocator.from_string('edX/test/course1')
    unit_id = '11111111111111111111111111111111'
    user = factory.SubFactory(UserFactory)
    survey_name = 'survey #1'
    survey_answer = '{"Q1": "1", "Q2": ["2", "3"], "Q3": "test"}'
    created = datetime(2014, 4, 1, tzinfo=UTC)
