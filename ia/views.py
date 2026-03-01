from django.shortcuts import render, get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from usuarios.models import Cliente
from usuarios.views import conectar_siscap
from .models import Pergunta, ContextRag
from django.http import JsonResponse, StreamingHttpResponse
from .agents import JuriAi, SecretariaAI
from typing import Iterator
from agno.agent import RunOutputEvent, RunEvent
from .models import AnaliseJurisprudencia, Documentos
from agno.agent import RunOutput
from .wrapper_evolution_api import SendMessage
import logging
import json
import urllib.parse
import re

def tratar_lista(texto, padrao=None):
    if not texto:
        return []
    
    # Se for uma lista com um único item, tenta processar esse item (para corrigir dados antigos)
    if isinstance(texto, list):
        if len(texto) == 1 and isinstance(texto[0], str):
            texto = texto[0]
        else:
            return texto
    
    # Se houver \n ou \r literais (strings), substitui por quebras reais
    texto = texto.replace('\\n', '\n').replace('\\r', '\r')
    
    if padrao:
        # Divide pelo padrão (case insensitive)
        partes = re.split(padrao, texto, flags=re.IGNORECASE)
        # Filtra partes vazias e remove espaços extras
        final = [p.strip() for p in partes if p.strip()]
        if len(final) > 0:
            return final
    
    # Fallback: divide por quebras de linha reais
    linhas = [l.strip() for l in texto.split('\n') if l.strip()]
    if len(linhas) > 1:
        return linhas
    
    # Se nada funcionou, retorna a string limpa em uma lista de um item
    item = texto.strip()
    return [item] if item else []

# Configuração do logger do Django
logger = logging.getLogger('django')

@csrf_exempt
def chat(request, id):
    cliente = Cliente.objects.get(id=id)
    if request.method == 'GET':
        return render(request, 'chat.html', {'cliente': cliente})
    elif request.method == 'POST':
        pergunta = request.POST.get('pergunta')
        pergunta_model = Pergunta(pergunta=pergunta, cliente=cliente)
        pergunta_model.save()
        return JsonResponse({'id': pergunta_model.id})

@csrf_exempt
def stream_resposta(request):
    id_pergunta = request.POST.get('id_pergunta')
    pergunta = get_object_or_404(Pergunta, id=id_pergunta)

    def gerar_resposta():
        agent = JuriAi.build_agent()
        stream: Iterator[RunOutputEvent] = agent.run(pergunta.pergunta, stream=True, stream_events=True)
        for chunk in stream:
            if chunk.event == RunEvent.run_content:
                yield str(chunk.content)
            if chunk.event == RunEvent.tool_call_completed:
                context = ContextRag(content=chunk.tool.result, tool_name=chunk.tool.tool_name, tool_args=chunk.tool.tool_args, pergunta=pergunta)
                context.save()

    response = StreamingHttpResponse(
        gerar_resposta(),
        content_type='text/plain; charset=utf-8'
    )
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'

    return response

def ver_referencias(request, id):
    pergunta = get_object_or_404(Pergunta, id=id)
    contextos = ContextRag.objects.filter(pergunta=pergunta)
    return render(request, 'ver_referencias.html', {
        'pergunta': pergunta,
        'contextos': contextos
    })

def analise_jurisprudencia(request, id):
    documento = get_object_or_404(Documentos, id=id)
    analise = AnaliseJurisprudencia.objects.filter(documento=documento).first()
    numero = documento.cliente.nome
    logger.info(f"analise_jurisprudencia - pedido: {numero} | tipo: {documento.tipo}")
    #logger.info(f"conteudo do 9.2: {documento.content}")
    query = f" * FROM anterioridades_desc where numero='{numero}'"
    query_json = json.dumps({"mysql_query": query})
    encoded_query = urllib.parse.quote(query_json)
    url = f"https://cientistaspatentes.com.br/apiphp/patents/query/?q={encoded_query}"
    
    resumo = ''
    historico_pedido = ''
    erros_coerencia = ''
    resumo_recurso = ''
    comparacao_docs = ''
    try:
        json_data = conectar_siscap(url)
        logger.info(f"json tipo: {type(json_data)}")
        logger.info(f"json dado: {json_data[:500]}")
        json_data = json_data.replace('\r', '\\r').replace('\n', '\\n')
        if json_data:
            data = json.loads(json_data)
            if "patents" in data and len(data["patents"]) > 0:
                resumo = data["patents"][0]["resumo_pedido"]
                historico_pedido = data["patents"][0]["razoes"]
                erros_coerencia = data["patents"][0]["incoerencia"]
                resumo_recurso = data["patents"][0]["resumo_recurso"]
                comparacao_docs = data["patents"][0]["comparacao_docs"]
    except Exception as e:
        logger.info(f"Erro ao buscar resumo_pedido: {e}")
    logger.info(f"resumo: {resumo}")

    # Se o resumo foi obtido da API, persiste em resumo_pedido se estiver vazio
    if resumo:
        if analise:
            # Registro de análise já existe: só grava se resumo_pedido estiver vazio
            if not analise.resumo_pedido:
                analise.resumo_pedido = tratar_lista(resumo)
                analise.save(update_fields=['resumo_pedido'])
                logger.info(f"Resumo gravado em resumo_pedido para pedido {numero}")
            else:
                logger.info(f"resumo_pedido já preenchido, ignorando resumo para pedido {numero}")
        else:
            # Ainda não existe análise: cria um registro mínimo com o resumo
            analise = AnaliseJurisprudencia.objects.create(
                documento=documento,
                indice_risco=0,
                classificacao='Pendente',
                resumo_pedido=tratar_lista(resumo)
            )
            logger.info(f"AnaliseJurisprudencia criada com resumo para pedido {numero}")

    logger.info("Iniciando busca de historico do pedido")
    #logger.info(f"historico_pedido: {historico_pedido}")

    if historico_pedido:
        if analise:
            # Registro de análise já existe: só grava se resumo_pedido estiver vazio
            if not analise.historico_pedido:
                analise.historico_pedido = tratar_lista(historico_pedido)
                analise.save(update_fields=['historico_pedido'])
                logger.info(f"Histórico salvo {numero}")
            else:
                logger.info(f"Histórico ignorado {numero}")
        else:
            # Ainda não existe análise: cria um registro mínimo com o resumo
            analise = AnaliseJurisprudencia.objects.create(
                documento=documento,
                indice_risco=0,
                classificacao='Pendente',
                historico_pedido=tratar_lista(historico_pedido)
            )
            logger.info(f"AnaliseJurisprudencia criada com historico {numero}")

    logger.info("Iniciando busca de erros_coerencia")
    #logger.info(f"erros_coerencia: {erros_coerencia}")

    if erros_coerencia:
        if analise:
            # Registro de análise já existe: só grava se resumo_pedido estiver vazio
            if not analise.erros_coerencia:
                analise.erros_coerencia = tratar_lista(erros_coerencia)
                analise.save(update_fields=['erros_coerencia'])
                logger.info(f"erros coerencia {numero}")
            else:
                logger.info(f"Erros coerencia ignorado {numero}")
        else:
            # Ainda não existe análise: cria um registro mínimo com o resumo
            analise = AnaliseJurisprudencia.objects.create(
                documento=documento,
                indice_risco=0,
                classificacao='Pendente',
                erros_coerencia=tratar_lista(erros_coerencia)
            )
            logger.info(f"AnaliseJurisprudencia criada com erros coerencia {numero}")

    logger.info("Iniciando busca de resumo_recurso")
    #logger.info(f"resumo_recurso: {resumo_recurso}")

    if resumo_recurso:
        if analise:
            # Registro de análise já existe: grava se estiver vazio ou se tiver apenas um item (para tentar dividir)
            if not analise.resumo_recurso or len(analise.resumo_recurso) <= 1:
                analise.resumo_recurso = tratar_lista(resumo_recurso, padrao=r'(?=\s*\b[ivx]+\)\s+)')
                analise.save(update_fields=['resumo_recurso'])
                logger.info(f"resumo_recurso {numero}")
            else:
                logger.info(f"resumo_recurso ignorado {numero}")
        else:
            # Ainda não existe análise: cria um registro mínimo com o resumo
            analise = AnaliseJurisprudencia.objects.create(
                documento=documento,
                indice_risco=0,
                classificacao='Pendente',
                resumo_recurso=tratar_lista(resumo_recurso, padrao=r'(?=\s*\b[ivx]+\)\s+)')
            )
            logger.info(f"AnaliseJurisprudencia criada com resumo_recurso {numero}")

    logger.info("Iniciando busca de comparacao_docs")
    #logger.info(f"comparacao_docs: {comparacao_docs}")

    if comparacao_docs:
        if analise:
            # Registro de análise já existe: grava se estiver vazio ou se tiver apenas um item (para tentar dividir)
            if not analise.comparacao_docs or len(analise.comparacao_docs) <= 1:
                analise.comparacao_docs = tratar_lista(comparacao_docs, padrao=r'(?=Resumo[o]*\s*D\s*\d+)')
                analise.save(update_fields=['comparacao_docs'])
                logger.info(f"comparacao_docs {numero}")
            else:
                logger.info(f"comparacao_docs ignorado {numero}")
        else:
            # Ainda não existe análise: cria um registro mínimo com o resumo
            analise = AnaliseJurisprudencia.objects.create(
                documento=documento,
                indice_risco=0,
                classificacao='Pendente',
                comparacao_docs=tratar_lista(comparacao_docs, padrao=r'(?=Resumo[o]*\s*D\s*\d+)')
            )
            logger.info(f"AnaliseJurisprudencia criada com comparacao_docs {numero}")

    total_analises = 0

    if analise.resumo_pedido:
        total_analises += 1
    if analise.erros_coerencia:
        total_analises += 1
    if analise.comparacao_docs:
        total_analises += 1
    if analise.historico_pedido:
        total_analises += 1
    if analise.resumo_recurso:
        total_analises += 1
       
    analise.indice_risco = total_analises
    analise.save(update_fields=['indice_risco'])
    return render(request, 'analise_jurisprudencia.html', {
        'documento': documento,
        'analise': analise,
        'resumo': resumo
    })

from usuarios.models import Documentos
from ia.agent_langchain import JurisprudenciaAI
from .models import AnaliseJurisprudencia
from django.shortcuts import redirect
from django.contrib import messages
from django.contrib.messages import constants
import time
import logging

# Configuração do logger do Django
logger = logging.getLogger('django')

def processar_analise(request, id):
    if request.method != 'POST':
        messages.add_message(request, constants.ERROR, 'Método não permitido.')
        return redirect('analise_jurisprudencia', id=id)
    
    try:
        documento = get_object_or_404(Documentos, id=id)
        numero = documento.cliente.nome
        start_time = time.time()

        logger.info(f"Chamando a LLM para documento {id}")
        
        # Busca o documento tipo 214 para o mesmo cliente
        recurso = Documentos.objects.filter(cliente=documento.cliente, tipo='214').order_by('-data_upload').first()
        conteudo_recurso = recurso.content if recurso else ""
        
        logger.info(f"Teor Indeferimento: {documento.content[:100]}...")
        logger.info(f"Teor Recurso: {conteudo_recurso[:100]}...")
        
        agent = JurisprudenciaAI()
        response = agent.run(indeferimento=documento.content, recurso=conteudo_recurso)
        
        processing_time = int(time.time() - start_time)
        
        #indice = response.indice_risco
        indice = 0
        if indice <= 30:
            classificacao = "Baixo"
        elif indice <= 60:
            classificacao = "Médio"
        elif indice <= 80:
            classificacao = "Alto"
        else:
            classificacao = "Crítico"

        sql = f" * FROM anterioridades_desc WHERE numero='{numero}'"
        query = urllib.parse.quote(json.dumps({"mysql_query": sql}))
        url = f"https://cientistaspatentes.com.br/apiphp/patents/query/?q={query}"
        logger.info(f"url historico_pedido: {url}")
        json_data = conectar_siscap(url)
        if json_data is None:
            historico_pedido = ''
            logger.info(f"historico vazio (1)")
        else:
            json_data = json_data.replace('\r', '\\r').replace('\n', '\\n')
            result = json.loads(json_data)
            if "patents" in result and len(result["patents"]) > 0:
                historico_pedido = result["patents"][0]["historico_pedido"]
                logger.info(f"historico_lido = {historico_pedido}")
            else:
                historico_pedido = ''
                logger.info(f"historico vazio (2)")
        
        analise, created = AnaliseJurisprudencia.objects.update_or_create(
            documento=documento,
            defaults={
                'indice_risco': indice,
                'classificacao': classificacao,
                'erros_coerencia': tratar_lista(response.erros_coerencia),
                'historico_pedido': tratar_lista(historico_pedido),
                'resumo_pedido': tratar_lista(response.resumo_pedido),
                'resumo_recurso': tratar_lista(response.resumo_recurso, padrao=r'(?=\s*\b[ivx]+\)\s+)'),
                #'comparacao_docs': response.comparacao_docs,
                'red_flags': tratar_lista(response.red_flags),
                'tempo_processamento': processing_time
            }
        )
        
        if created:
            messages.add_message(request, constants.SUCCESS, 'Análise realizada e salva com sucesso!')
        else:
            messages.add_message(request, constants.SUCCESS, 'Análise atualizada com sucesso!')
        
        return redirect('analise_jurisprudencia', id=id)
    except Exception as e:
        messages.add_message(request, constants.ERROR, f'Erro ao processar análise: {str(e)}')
        return redirect('analise_jurisprudencia', id=id)
    
import json
from django.http import HttpResponse

@csrf_exempt
def webhook_whatsapp(request):
    return HttpResponse(status=204)
    #data = json.loads(request.body)
    #phone = data.get('data').get('key').get('remoteJid').split('@')[0]
    #message = data.get('data').get('message').get('extendedTextMessage').get('text')
    #agent = SecretariaAI.build_agent(session_id=phone)
    #response: RunOutput = agent.run(message)
    #return JsonResponse({'response': response.content})
    #send_message = SendMessage().send_message('Arcane3', {'number': phone, 'textMessage': {'text': response}})

def get_analise_json(request, id):
    analise = get_object_or_404(AnaliseJurisprudencia, id=id)
    data = {
        'id': analise.id,
        'documento_id': analise.documento.id,
        'cliente': analise.documento.cliente.nome,
        'tipo_documento': analise.documento.get_tipo_display(),
        'indice_risco': analise.indice_risco,
        'classificacao': analise.classificacao,
        'erros_coerencia': analise.erros_coerencia,
        'historico_pedido': analise.historico_pedido,
        'resumo_pedido': analise.resumo_pedido,
        'resumo_recurso': analise.resumo_recurso,
        'comparacao_docs': analise.comparacao_docs,
        'red_flags': analise.red_flags,
        'tempo_processamento': analise.tempo_processamento,
        'data_criacao': analise.data_criacao.strftime('%d/%m/%Y %H:%M')
    }
    return JsonResponse(data)

