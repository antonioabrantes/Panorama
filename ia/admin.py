from django.contrib import admin
from .models import Pergunta, ContextRag, AnaliseJurisprudencia

@admin.register(AnaliseJurisprudencia)
class AnaliseJurisprudenciaAdmin(admin.ModelAdmin):
    list_display = ('id', 'documento', 'classificacao', 'indice_risco', 'data_criacao')
    list_filter = ('classificacao', 'data_criacao')
    search_fields = ('documento__cliente__nome', 'documento__arquivo')
    readonly_fields = ('data_criacao', 'data_atualizacao')

admin.site.register(Pergunta)
admin.site.register(ContextRag)