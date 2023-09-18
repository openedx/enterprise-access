"""
enterprise_access URL Configuration.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/2.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  url(r'^$', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  url(r'^$', Home.as_view(), name='home')
Including another URLconf
    1. Add an import:  from blog import urls as blog_urls
    2. Add a URL to urlpatterns:  url(r'^blog/', include(blog_urls))
"""

import os

from auth_backends.urls import oauth2_urlpatterns
from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView
from edx_api_doc_tools import make_api_info, make_docs_urls

from enterprise_access.apps.api import urls as api_urls
from enterprise_access.apps.core import views as core_views

api_info = make_api_info(title="Enterprise Access API", version="v1")
admin.autodiscover()

spectacular_view = SpectacularAPIView(
    api_version='v1',
    title='enterprise-access spectacular view',
)

spec_swagger_view = SpectacularSwaggerView()

spec_redoc_view = SpectacularRedocView(
    title='Redoc view for the enterprise-access API.',
    url_name='schema',
)

urlpatterns = oauth2_urlpatterns + make_docs_urls(api_info) + [
    re_path(r'^admin/', admin.site.urls),
    path('api/', include(api_urls)),
    re_path(r'^api-docs/', spec_swagger_view.as_view(), name='swagger-ui'),
    path('auto_auth/', core_views.AutoAuth.as_view(), name='auto_auth'),
    path('', include('csrf.urls')),  # Include csrf urls from edx-drf-extensions
    path('health/', core_views.health, name='health'),
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/schema/redoc/', spec_redoc_view.as_view(url_name='schema'), name='redoc'),
]

if settings.DEBUG and os.environ.get('ENABLE_DJANGO_TOOLBAR', False):  # pragma: no cover
    # Disable pylint import error because we don't install django-debug-toolbar
    # for CI build
    import debug_toolbar
    urlpatterns.append(path('__debug__/', include(debug_toolbar.urls)))
