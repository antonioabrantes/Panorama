from django.urls import path
from . import views

urlpatterns = [
    path('cadastro/', views.cadastro, name='cadastro'),
    path("login/", views.login, name='login'),
    path("clientes/", views.clientes, name='clientes'),
    path("cliente/<int:id>", views.cliente, name='cliente'),
    path("novo_documento/<int:id>", views.novo_documento, name='novo_documento'),
    path("excluir_documento/<int:id>", views.excluir_documento, name='excluir_documento'),
    path("ver_texto/<int:id>", views.ver_texto_documento, name='ver_texto_documento'),
    path("ver_pdf/<int:id>", views.ver_pdf_documento, name='ver_pdf_documento'),
    path("teste_check/", views.teste_check_documento, name='teste_check_documento'),
    path("logar_clientes/", views.logar_clientes, name='logar_clientes'),
]