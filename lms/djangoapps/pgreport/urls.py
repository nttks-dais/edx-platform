from django.conf.urls import url, patterns
from django.conf import settings

urlpatterns = patterns('',  # nopep8
    url(r'^get_course_structure/{}/$'.format(settings.COURSE_ID_PATTERN),
       'pgreport.views.ajax_get_course_structure',
        name="get_course_structure"),
    url(r'^get_problem_data/{}/$'.format(settings.COURSE_ID_PATTERN),
        'pgreport.views.ajax_get_problem_data',
        name="get_problem_data"),
    url(r'^get_progress_list/{}/$'.format(settings.COURSE_ID_PATTERN),
        'pgreport.views.ajax_get_progress_list',
        name="get_progress_list"),
    url(r'^download_progress_report/{}/$'.format(settings.COURSE_ID_PATTERN),
        'pgreport.views.ajax_get_pgreport_csv',
        name="get_pgreport_csv"),
)
