from django.urls import include, path

from accounts import views as account_views

urlpatterns = [
    path("login/", account_views.login_view, name="login"),
    path("logout/", account_views.logout_view, name="logout"),
    path("i18n/", include("django.conf.urls.i18n")),
    path("", include("inventory.urls")),
]
