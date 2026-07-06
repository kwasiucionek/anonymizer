from django.urls import path

from . import views

app_name = "dokumenty"

urlpatterns = [
    path("", views.pulpit, name="pulpit"),
    path("dokument/<int:pk>/", views.dokument_detail, name="dokument"),
    path("dokument/<int:pk>/wiersz/", views.dokument_wiersz, name="dokument-wiersz"),
    path(
        "dokument/<int:pk>/zatrzymaj/",
        views.dokument_zatrzymaj,
        name="dokument-zatrzymaj",
    ),
    path("dokument/<int:pk>/ponow/", views.dokument_ponow, name="dokument-ponow"),
    path("dokument/<int:pk>/usun/", views.dokument_usun, name="dokument-usun"),
    path(
        "dokumenty/usun-wybrane/",
        views.dokumenty_usun_wybrane,
        name="dokumenty-usun-wybrane",
    ),
    path(
        "dokument/<int:pk>/oryginal/",
        views.dokument_oryginal,
        name="dokument-oryginal",
    ),
    path("dokument/<int:pk>/wynik/", views.dokument_wynik, name="dokument-wynik"),
    path("sprawy/", views.sprawa_lista, name="sprawy"),
    path("sprawy/<int:pk>/", views.sprawa_detail, name="sprawa"),
    path(
        "sprawy/<int:pk>/osoba/usun/",
        views.sprawa_osoba_usun,
        name="sprawa-osoba-usun",
    ),
    path("sprawy/<int:pk>/cache.json", views.sprawa_cache_json, name="sprawa-cache"),
]
