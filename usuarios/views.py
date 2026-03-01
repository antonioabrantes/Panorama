from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.http import HttpResponse
from django.contrib.auth.models import User
from django.contrib.messages import constants
from django.contrib import messages
from django.contrib.auth import authenticate
from django.contrib import auth
from .models import Cliente, Documentos
from ia.agents import JuriAi
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
import requests, os
import json
import urllib.parse
import urllib3
from datetime import datetime
import logging
from django.utils import timezone
from django.conf import settings
import pypdfium2 as pdfium
from ia.tasks import anonimizar_documento
import re

# Configuração do logger do Django
logger = logging.getLogger('django')

def buscar_foto(login):
    query = f" * from servidores where email='{login}' and rescisao='0000-00-00' and complemento in ('CGREC/DIREP','CGREC/')"
    query_json = json.dumps({"mysql_query": query})
    encoded_query = urllib.parse.quote(query_json)
    url = f"https://cientistaspatentes.com.br/apiphp/patents/query/?q={encoded_query}"
    logger.info(url)
    matricula = ''
    try:
        json_data = conectar_siscap(url)
        if json_data:
            # Trata o erro de escape \N comum no MySQL
            #json_data = json_data.replace('\\N', 'null')
            json_data = re.sub(r'\\N', 'null', json_data)
            json_data = json_data.replace('\r', '\\r').replace('\n', '\\n')

            data = json.loads(json_data)
            if "patents" in data and len(data["patents"]) > 0:
                #matricula = data["patents"][0]["matricula"]
                matricula = data["patents"][0].get("matricula")
            else:
                return None

    except Exception as e:
        logger.error(f"erro {e} ao buscar servidor {url}")
        return None 
    
    logger.info(f"Servidor: {login} | Matricula: {matricula}")
    #arquivo_foto = f"{matricula}r1-removebg-preview.png"
    matriculas_aprovadas = {'1285038'}  # set
    arquivo_foto = f"{matricula}r1-removebg-preview.png" if str(matricula) in matriculas_aprovadas else 'user2.png'
    return arquivo_foto

def baixar_peticao(numero,numnossonumero,tipo_peticao,cd_imagem):
    try:
        url = f"http://br00-aux.inpi.gov.br/webservice/retornaImagem.php?codigo={cd_imagem}"
        relative_path_pdf = f'pareceres/peticoes/{numero}_{numnossonumero}_{tipo_peticao}.pdf'
        full_path_pdf = os.path.join(settings.MEDIA_ROOT, relative_path_pdf)
        
        # Tenta baixar com timeout
        response = requests.get(url, stream=True, verify=False, timeout=30)
        response.raise_for_status()
        
        if not os.path.exists(full_path_pdf):
            with open(full_path_pdf, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            logger.info(f"Download concluído: {full_path_pdf}")
        else:
            logger.info(f"O arquivo {full_path_pdf} já existe. Nada foi gravado.")       
        
        relative_path_txt = f'pareceres/peticoes/{numero}_{numnossonumero}_{tipo_peticao}.txt'
        full_path_txt = os.path.join(settings.MEDIA_ROOT, relative_path_txt)
        texto = ''

        try:
            pdf = pdfium.PdfDocument(full_path_pdf)
            for i in range(len(pdf)):
                page = pdf.get_page(i)
                textpage = page.get_textpage()
                texto += textpage.get_text_bounded() + "\n"
            logger.info(f"Texto extraído do PDF: {len(texto)} caracteres.")
            
            # Regra de Anonimização
            texto = anonimizar_documento(texto)
            logger.info("Anonimização aplicada ao conteúdo do PDF.")
                
        except Exception as e:
            logger.error(f"Erro ao extrair texto do PDF: {e}")
            texto = ""

        if not texto or not texto.strip():
            logger.warning(f"Conteúdo vazio: {url}")
            return None
        
        # Cria pasta e salva
        # os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path_txt, 'w', encoding='utf-8') as f:
            f.write(texto)
        
        return texto
        
    except requests.exceptions.ConnectionError:
        logger.error(f"Erro de conexão - servidor fora do ar: {url}")
    except requests.exceptions.Timeout:
        logger.error(f"Timeout - servidor demorou muito: {url}")
    except requests.exceptions.HTTPError as e:
        logger.error(f"Erro HTTP {e.response.status_code}: {url}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro na requisição: {url} - {str(e)}")
    except (PermissionError, OSError) as e:
        logger.error(f"Erro ao salvar arquivo: {full_path} - {str(e)}")
    except Exception as e:
        logger.error(f"Erro inesperado: {str(e)}")
    
    return None

def baixar_parecer(divisao, numero, codigo):
    try:
        numero_processo = f"{divisao}/{numero}{codigo}" 
        url = f"https://siscap.inpi.gov.br/adm/pareceres/{numero_processo}.txt"
        relative_path = f'pareceres/{numero_processo}.txt'
        full_path = os.path.join(settings.MEDIA_ROOT, relative_path)
        
        # Tenta baixar com timeout
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        
        texto = response.text
        
        if not texto or not texto.strip():
            logger.warning(f"Conteúdo vazio: {url}")
            return None
        
        # Cria pasta e salva
        # os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(texto)
        
        return texto
        
    except requests.exceptions.ConnectionError:
        logger.error(f"Erro de conexão - servidor fora do ar: {url}")
    except requests.exceptions.Timeout:
        logger.error(f"Timeout - servidor demorou muito: {url}")
    except requests.exceptions.HTTPError as e:
        logger.error(f"Erro HTTP {e.response.status_code}: {url}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro na requisição: {url} - {str(e)}")
    except (PermissionError, OSError) as e:
        logger.error(f"Erro ao salvar arquivo: {full_path} - {str(e)}")
    except Exception as e:
        logger.error(f"Erro inesperado: {str(e)}")
    
    return None


def cliente_existe(nome_cliente, numero):
    """
    Verifica se um numero já existe para na carga de nome
    """
    try:
       
        # Busca o cliente pelo username do usuário e nome exato
        cliente = Cliente.objects.filter(user__username=nome_cliente, nome=numero).first()
        if not cliente:
            return False, "Cliente não encontrado"
        else:
            return True, "Documento encontrado"
       
    except Exception as e:
        return False, f"Erro na verificação: {e}"

def documento_existe(nome_cliente, tipo, data_str):
    """
    Verifica se um documento já existe para um cliente pelo nome, tipo e data.
    Esperado data_str no formato 'DD/MM/YYYY'
    """
    try:
        # Converte a string de data para objeto datetime (considerando apenas o dia)
        data_obj = None
        if data_str and '/' in data_str:
            data_obj = datetime.strptime(data_str, '%d/%m/%Y') # converte String original: 15/03/2024 em 2024-03-15 00:00:00
        else:
            data_obj = datetime.strptime(data_str, '%Y-%m-%d') # converte String original: 2024-03-15 em 2024-03-15 00:00:00
        
        # Busca o cliente pelo nome
        cliente = Cliente.objects.filter(nome=nome_cliente).first()
        if not cliente:
            return False, "Cliente não encontrado"

        # Filtra os documentos
        query = Documentos.objects.filter(
            cliente=cliente,
            tipo=tipo
        )
        
        # Se tiver data, filtra por data (considerando o dia)
        if data_obj:
            # No Django, para DateTimeField, usamos __date para comparar apenas a data
            query = query.filter(data_upload__date=data_obj.date())

        existe = query.exists()
        return existe, "Documento encontrado" if existe else "Documento não encontrado"
        
    except Exception as e:
        return False, f"Erro na verificação: {e}"

def teste_check_documento(request, nome, tipo, data):
    # Valores solicitados pelo usuário

    existe, msg = documento_existe(nome, tipo, data)
    resultado = 'EXISTE' if existe else 'NÃO EXISTE'
    
    # Envia para o log do Django
    logger.info(f"--- TESTANDO DOCUMENTO ---")
    logger.info(f"Cliente: {nome}")
    logger.info(f"Tipo: {tipo}")
    logger.info(f"Data: {data}")
    logger.info(f"Resultado: {resultado}")
    logger.info(f"Mensagem: {msg}")
    logger.info(f"--------------------------")
    
    return HttpResponse(f"Log de verificação gerado no terminal para o cliente {nome}.")

def logar_clientes(request):
    """
    Lê o conteúdo atual da classe Cliente e mostra no logger.info
    """
    clientes = Cliente.objects.all()
    
    logger.info("========== LISTAGEM DE CLIENTES ==========")
    for cliente in clientes:
        logger.info(
            f"ID: {cliente.id} | "
            f"Nome: {cliente.nome} | "
            f"Email: {cliente.email} | "
            f"Número: {cliente.numero} | "
            f"Tipo: {cliente.tipo} | "
            f"Status: {'Ativo' if cliente.status else 'Inativo'} | "
            f"Usuário: {cliente.user.username}"
        )
    logger.info(f"Total de clientes: {clientes.count()}")
    logger.info("==========================================")
    
    return HttpResponse(f"Foram logados {clientes.count()} clientes no terminal.")

def conectar_siscap(url):
    headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    # Desabilita avisos de segurança conforme padrão do projeto
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    
    try:
        response = requests.get(url, headers=headers, verify=False, timeout=10)
        if response.status_code == 200:
            return response.text
        else:
            logger.error(f"Erro ao conectar ao Siscap (Status {response.status_code}): {url}")
            logger.error(f"Resposta: {response.text[:500]}")
    except Exception as e:
        logger.error(f"Exceção ao conectar ao Siscap: {e} | URL: {url}")
    return None

def cadastro(request):
    if request.method == 'GET':
        return render(request, 'cadastro.html')
    elif request.method == 'POST':
        username = request.POST.get('username')
        senha = request.POST.get('senha')
        confirmar_senha = request.POST.get('confirmar_senha')

        if not senha == confirmar_senha:
            messages.add_message(request, constants.ERROR, 'Senha e confirmar senha não são iguais.')
            return redirect('cadastro')
        
        if len(senha) < 4:
            messages.add_message(request, constants.ERROR, 'Sua senha deve ter pelo menos 4 caracteres.')
            return redirect('cadastro')
        
        users = User.objects.filter(username=username)
        
        if users.exists():
            messages.add_message(request, constants.ERROR, 'Já existe um usuário com esse username.')
            return redirect('cadastro')
        
        User.objects.create_user(
            username=username,
            password=senha
        )

        return redirect('login')

@ensure_csrf_cookie
def login(request):
    if request.method == 'GET':
        return render(request, 'login.html')
    elif request.method == 'POST':
        username = request.POST.get('username')
        senha = request.POST.get('senha')

        user = authenticate(username=username, password=senha)
        if user is not None:
            auth.login(request, user)
            return redirect('clientes')
        else:
            messages.add_message(request, constants.ERROR, 'Usuário ou senha inválidos.')
            return redirect('login')
        

@csrf_exempt
def clientes(request):
    login = request.user.username
    query_str = f" count(*) as total FROM carga where examinador='{login}'"
    query_json = json.dumps({"mysql_query": query_str})
    encoded_query = urllib.parse.quote(query_json)
    url = f"https://cientistaspatentes.com.br/apiphp/patents/query/?q={encoded_query}"
    
    total_pedidos = 0
    try:
        json_data = conectar_siscap(url)
        if json_data:
            json_data = json_data.replace('\r', '\\r').replace('\n', '\\n')
            data = json.loads(json_data)
            if "patents" in data and len(data["patents"]) > 0:
                total_pedidos = data["patents"][0]["total"]
    except Exception as e:
        print(f"Erro ao buscar total de pedidos: {e}")

    patents = []
    query_str = f" * FROM carga where examinador='{login}'"
    query_json = json.dumps({"mysql_query": query_str})
    encoded_query = urllib.parse.quote(query_json)
    url = f"https://cientistaspatentes.com.br/apiphp/patents/query/?q={encoded_query}"
    try:
        json_data = conectar_siscap(url)
        if json_data:
            json_data = json_data.replace('\r', '\\r').replace('\n', '\\n')
            result = json.loads(json_data)
            patents = result.get("patents", [])
    except Exception as e:
        print(f"Erro ao buscar total de pedidos: {e}")

    logar_clientes(request)
    existe, msg = cliente_existe('abrantes', '102012013942')
    logger.info(f"testando carga abrantes 102012013942 {existe}")
    existe, msg = cliente_existe('abrantes', '102013019766')
    logger.info(f"testando carga abrantes 102013019766 {existe}")

    if request.method == 'GET':
        # Reseta o status de todos os clientes do usuário para False antes da sincronização
        Cliente.objects.filter(user=request.user).update(status=False)
        logger.info(f"Status de todos os clientes de {login} resetados para False para sincronização.")

        clientes = Cliente.objects.filter(user=request.user)

        for patent in patents:
            numero = patent.get("numero", "")
            logger.info(f"testando {numero}")
            existe, msg = cliente_existe(login, numero)
            resultado = 'EXISTE' if existe else 'NAO EXISTE'
            if resultado=='NAO EXISTE':
                logger.info(f"criar novo cliente {numero} {login}")
                Cliente.objects.create(
                    numero=numero,
                    nome = numero,
                    email = f"{login}@inpi.gov.br",
                    tipo = "PF",
                    status = True,
                    user = request.user
    		    )
            else:
                logger.info(f"registro já existe! Ativando status para {numero}")
                # Reativa o registro que já existe e consta na carga da API
                Cliente.objects.filter(user=request.user, numero=numero).update(status=True)

        clientes = Cliente.objects.filter(user=request.user)
        arquivo_foto = buscar_foto(login)
        return render(request, 'clientes.html', {
            'clientes': clientes, 
            'total_pedidos': total_pedidos,
            'arquivo_foto': arquivo_foto
        })

    elif request.method == 'POST':
        nome = request.POST.get('nome')
        email = request.POST.get('email')
        numero = request.POST.get('numero')
        tipo = request.POST.get('tipo')
        status = request.POST.get('status') == 'on'

        Cliente.objects.create(
            nome=nome,
            email=email,
            numero=numero,
            tipo=tipo,
            status=status,
            user=request.user
        )

        messages.add_message(request, constants.SUCCESS, 'Cliente cadastrado com sucesso!')
        return redirect('clientes')

@csrf_exempt
def cliente(request, id):
    cliente = Cliente.objects.get(id=id)
    print(f"veja no terminal entrando em cliente.html - Pedido: {cliente.nome}, Id: {id}")
    numero = cliente.nome
    
    nome = '102012013942'
    tipo = '7.1'
    data = '18/02/2026'
    teste_check_documento(request, nome, tipo, data)

    # baixa os pareceres que possam existir para este numero
    sql = (
        " * FROM `pedido` "
        f"WHERE numero='{numero}' "
        "AND decisao IN ("
        "'exigencia','ciencia de parecer','indeferimento','9.2',"
        "'recurso exigencia','recurso ciencia','recurso provido',"
        "'recurso provido anvisa','recurso negado',"
        "'recurso exigencia 121','recurso provido-reforma 100.1',"
        "'recurso provido-devolucao 100.2',"
        "'recurso manutencao do indeferimento 111')"
    )

    query = urllib.parse.quote(json.dumps({"mysql_query": sql}))
    url = f"https://cientistaspatentes.com.br/apiphp/patents/query/?q={query}"
    logger.info(f"url: {url}")
    try:
        json_data = conectar_siscap(url)
        json_data = json_data.replace('\r', '\\r').replace('\n', '\\n')
        result = json.loads(json_data)
        for i in range(len(result["patents"])):
            data = result["patents"][i]["rpi"]
            decisao = result["patents"][i]["decisao"]
            divisao = result["patents"][i]["divisao"]
            codigo = result["patents"][i]["codigo"]
            documento = f"pareceres/{divisao}/{numero}{codigo}.txt"
            documento_pdf = f"https://siscap.inpi.gov.br/adm/pareceres/{divisao}/00_{numero}{codigo}.pdf"
            
            if (decisao=='exigencia'):
                tipo = '6.1'
            elif (decisao=='ciencia de parecer'):
                tipo = '7.1'
            elif (decisao=='indeferimento' or decisao=='9.2'):
                tipo = '9.2'
            elif (decisao=='recurso exigencia' or decisao=='recurso exigencia 121' or decisao=='recurso ciencia'):
                tipo = '121'
            elif (decisao=='recurso provido' or decisao=='recurso provido anvisa' or decisao=='recurso provido-reforma 100.1'):
                tipo = '100.1'
            elif (decisao=='recurso provido-devolucao 100.2'):
                tipo = '100.2'
            elif (decisao=='recurso negado' or decisao=='recurso manutencao do indeferimento 111'):
                tipo = '111'
            else:
                continue

            logger.info(f"testando {numero} {tipo} {data}")
            existe, msg = documento_existe(numero, tipo, data)
            resultado = 'EXISTE' if existe else 'NAO EXISTE'
            if resultado=='NAO EXISTE':
                data = datetime.strptime(data, "%Y-%m-%d")
                data = timezone.make_aware(data)
                texto = baixar_parecer(divisao,numero,codigo)
                if texto:
                    logger.info(f"criar novo registro {tipo} {data}")
                    documentos = Documentos(
                        cliente=cliente,
                        codigo=codigo,
                        tipo=tipo,
                        arquivo=documento,
                        data_upload=data,
                        documento_pdf=documento_pdf,
                        content=texto
                    )
                    documentos.save()
                    messages.add_message(request, constants.SUCCESS, 'Documento adicionado com sucesso!')
                else:
                    logger.error(f"Erro ao baixar o parecer {numero} {tipo} {data}")
            else:
                logger.info(f"registro já existe ! {tipo} {data}")

    except Exception as e:
        logger.error(f"Erro ao processar pedidos: {e}")

    # baixa as petições que possam existir para este numero
    # https://www.gov.br/inpi/pt-br/inpi-data/precificacao-dos-servicos/NovaTabeladeRetribuiesINPI_PortariaINPI_Final_20_dez_25.pdf
    sql = (
        " * FROM `despachos_pag` "
        f"WHERE numero='{numero}' "
        "AND tipo_peticao IN ("
        "'200','202','203','204','205','207','210','214','215','216','260','272','280','281','282','284','285','295','296')"
    )

    query = urllib.parse.quote(json.dumps({"mysql_query": sql}))
    url = f"https://cientistaspatentes.com.br/apiphp/patents/query/?q={query}"
    logger.info(f"url: {url}")
    try:
        json_data = conectar_siscap(url)
        json_data = json_data.replace('\r', '\\r').replace('\n', '\\n')
        result = json.loads(json_data)
        for i in range(len(result["patents"])):
            data_peticao = result["patents"][i]["data_peticao"]
            tipo_peticao = result["patents"][i]["tipo_peticao"]
            peticao = result["patents"][i]["peticao"]
            numnossonumero = result["patents"][i]["numnossonumero"]
            cd_imagem = result["patents"][i]["cd_imagem"]
            documento = f"pareceres/peticoes/{numero}_{numnossonumero}_{tipo_peticao}.txt"
            documento_pdf = f"http://br00-aux.inpi.gov.br/webservice/retornaImagem.php?codigo={cd_imagem}"
            tipo = tipo_peticao
            logger.info(f"testando {numero} {tipo_peticao} {data_peticao} {peticao} {numnossonumero} {cd_imagem}")
            existe, msg = documento_existe(numero, tipo_peticao, data_peticao)
            resultado = 'EXISTE' if existe else 'NAO EXISTE'
            if resultado=='NAO EXISTE':
                data = datetime.strptime(data_peticao, "%Y-%m-%d")
                data = timezone.make_aware(data, timezone.get_current_timezone())
                texto = baixar_peticao(numero,numnossonumero,tipo_peticao,cd_imagem)
                if texto is None or texto == '':
                    logger.error(f"Erro ao baixar o parecer {numero} {tipo} {data}")
                    texto = 'PDF imagem'
                logger.info(f"criar novo registro {tipo_peticao} {data_peticao}")
                documentos = Documentos(
                    cliente=cliente,
                    codigo=cd_imagem,
                    tipo=tipo_peticao,
                    arquivo=documento,
                    data_upload=data,
                    documento_pdf=documento_pdf,
                    numnossonumero=numnossonumero,
                    peticao=peticao,
                    content=texto
                )
                documentos.save()
                messages.add_message(request, constants.SUCCESS, 'Documento adicionado com sucesso!')
            else:
                logger.info(f"registro já existe ! {tipo} {data}")

    except Exception as e:
        logger.error(f"Erro ao processar petições: {e}")

    login = cliente.user.username
    arquivo_foto = buscar_foto(login)
    documentos = Documentos.objects.filter(cliente=cliente).order_by('-data_upload')
    return render(request, 'cliente.html', {
        'cliente': cliente, 
        'documentos': documentos,
        'arquivo_foto': arquivo_foto
    })

@csrf_exempt
def novo_documento(request, id):
    cliente = Cliente.objects.get(id=id)
    if request.method == 'GET':
        return render(request, 'novo_documento.html', {'cliente': cliente})
    elif request.method == 'POST':
        tipo = request.POST.get('tipo')
        documento = request.FILES.get('documento')
        data = request.POST.get('data')
        
        texto = ""
        if documento and documento.name.lower().endswith('.pdf'):
            try:
                pdf = pdfium.PdfDocument(documento.file)
                for i in range(len(pdf)):
                    page = pdf.get_page(i)
                    textpage = page.get_textpage()
                    texto += textpage.get_text_bounded() + "\n"
                logger.info(f"Texto extraído do PDF: {len(texto)} caracteres.")
                
                # Regra de Anonimização
                tipos_preservados = ['6.1', '7.1', '9.2', '111', '121', '100.1', '100.2']
                if tipo not in tipos_preservados:
                    texto = anonimizar_documento(texto)
                    logger.info("Anonimização aplicada ao conteúdo do PDF.")
                else:
                    logger.info(f"Tipo {tipo} detectado. Pulando anonimização.")
                    
            except Exception as e:
                logger.error(f"Erro ao extrair texto do PDF: {e}")
                texto = f"Erro na extração: {e}"

        documentos = Documentos(
            cliente=cliente,
            tipo=tipo,
            arquivo=documento,
            data_upload=data,
            content=texto
        )

        documentos.save()
        messages.add_message(request, constants.SUCCESS, 'Documento adicionado com sucesso!')
        return redirect(reverse('cliente', kwargs={'id': cliente.id}))
    

def excluir_documento(request, id):
    documento = get_object_or_404(Documentos, id=id)
    cliente_id = documento.cliente.id
    
    # Tenta remover da memória RAG (Vectordb)
    try:
        # No Agno, podemos tentar deletar usando o nome do arquivo como filtro
        # se o provider suportar. 
        JuriAi.knowledge.vector_db.delete(filter={"name": documento.arquivo.name})
    except Exception as e:
        print(f"Erro ao deletar da memória RAG: {e}")

    # Remove o arquivo físico (.pdf e .txt se existir)
    if documento.arquivo:
        import os
        txt_path = os.path.splitext(documento.arquivo.path)[0] + '.txt'
        if os.path.exists(txt_path):
            os.remove(txt_path)
        documento.arquivo.delete()
    
    # Remove do banco de dados
    documento.delete()
    
    messages.add_message(request, constants.SUCCESS, 'Documento excluído com sucesso!')
    return redirect(reverse('cliente', kwargs={'id': cliente_id}))

def ver_texto_documento(request, id):
    documento = get_object_or_404(Documentos, id=id)
    # Tenta ler o arquivo físico TXT
    import os
    file_path = documento.arquivo.path
    txt_path = os.path.splitext(file_path)[0] + '.txt'
    logger.info(f"arquivo.path = {documento.arquivo.path}")
    logger.info(f"txt_path = {txt_path}")
    logger.info(f"existe? {os.path.exists(txt_path)}")
    # txt_path = C:\Users\otimi\arcabe3-completo-master\media\pareceres\direp\1020120139421979498.txt
    conteudo = ""
    if os.path.exists(txt_path):
        try:
            #with open(txt_path, 'r', encoding='utf-8') as f:
            with open(txt_path, 'r', encoding='utf-8', errors='ignore') as f:
                conteudo = f.read()
        except Exception as e:
            conteudo = f"Erro ao ler o arquivo: {e}"
    else:
        # Fallback para o conteúdo do banco se o arquivo físico não existir
        conteudo = documento.content if documento.content else "Este documento ainda não foi processado pelo OCR."

    return render(request, 'ver_texto.html', {
        'documento': documento,
        'conteudo': conteudo
    })

def ver_pdf_documento(request, id):
    documento = get_object_or_404(Documentos, id=id)
    pdf_url = documento.get_pdf_link()
    
    return render(request, 'ver_pdf.html', {
        'documento': documento,
        'pdf_url': pdf_url
    })
