from django.urls import path

from . import views

app_name = "inventory"

urlpatterns = [
    path("", views.stock, name="stock"),
    path("stock/export.xlsx", views.export_stock, name="export"),
    path("receive/", views.receive, name="receive"),
    path("issue/", views.issue, name="issue"),
    path("products/", views.products, name="products"),
    path("products/new/", views.product_create, name="product_create"),
    path("products/<int:pk>/edit/", views.product_edit, name="product_edit"),
    path("products/<int:pk>/archive/", views.product_archive, name="product_archive"),
    path("products/<int:pk>/restore/", views.product_restore, name="product_restore"),
    path("products/<int:pk>/delete/", views.product_delete, name="product_delete"),
    path("history/", views.history, name="history"),
    path("history/<int:pk>/cancel/", views.cancel_operation, name="cancel"),
]
