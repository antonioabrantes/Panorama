import re
import os
import unicodedata
from usuarios.models import Documentos
from django.shortcuts import get_object_or_404
from django.conf import settings
from .agents import JuriAi
from docling.document_converter import DocumentConverter

# VERSÃO DO SCRIPT: 2.1 (Ajuste de retornos e logs)

def anonimizar_documento(texto):
    if not texto: return ""
    
    def normalizar(texto):
        texto = texto.replace('\xa0', ' ')
        texto = unicodedata.normalize('NFKD', texto)
        texto = texto.encode('ASCII', 'ignore').decode('ASCII')
        return texto

    def anonymize_remover_cabecalhos(texto):
        padroes = [
            r"Assinado digitalmente por.*",
            r"Documento assinado eletronicamente.*",
            r"Protocolo:\s*\d+",
            r"URL para download:.*",
            r"Hash de autenticação:.*",
        ]
        for padrao in padroes:
            texto = re.sub(padrao, "", texto, flags=re.IGNORECASE)
        return texto

    def anonymize_cpfs(texto):
        return re.sub(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b", "[CPF]", texto)

    def anonymize_cnpj(texto):
        return re.sub(r"\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b", "[CNPJ]", texto)

    def anonymize_processos(texto):
        return re.sub(r"\b\d{9}\b", "[PROCESSO]", texto)

    def anonymize_emails(texto):
        return re.sub(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", "[EMAIL]", texto)

    def anonymize_documentos_identificacao(texto):
        regex = r"(CPF\/CNPJ|CPF|CNPJ)\s*:\s*([A-Z0-9\-\.\/]+)"
        return re.sub(regex, "[DOC_ID]", texto, flags=re.IGNORECASE)

    def anonymize_endereco(texto):
        regex = r"(Endere[cç]o\s*:\s*)(.+)"
        return re.sub(regex, "[ENDERECO]", texto, flags=re.IGNORECASE)

    def anonymize_remover_linhas_com_cep(texto):
        linhas = texto.splitlines()
        linhas_filtradas = [l for l in linhas if not re.search(r'\bCEP\b', l, flags=re.IGNORECASE)]
        return "\n".join(linhas_filtradas)
   
    def anonymize_remover_cabecalhos_pagina(texto):
        linhas = texto.splitlines()
        linhas_filtradas = [l for l in linhas if not re.search(r'^\s*Peticao\b', l, flags=re.IGNORECASE)]
        return "\n".join(linhas_filtradas)
       
    def anonymize_telefone(texto):
        regex = r"\b(Fone\/Fax|Fone|Telefone|Tel\.?|Fax)\b\s*:?\s*([\(\d][\d\.\-\)\s]+)"
        return re.sub(regex, "[TELEFONE]", texto, flags=re.IGNORECASE)

    def anonymize_nomes_rotulados(texto):
        regex = r"(Requerente|Tecnico|Inventor|Procurador)\s*:\s*([A-ZÁÉÍÓÚÂÊÔÃÕÇ\s]+)"
        return re.sub(regex, "[PESSOA_NATURAL]", texto)

    def iniciar_apos_recurso(texto):
        padrao = r"(RECURSO\s+do\s+despacho\s+que\s+indeferiu\s+o\s+Pedido\s+de\s+Paten\s*te|Excelentissimo|Ilmo\s+Senhor\s+presidente|recurso\s+ao\s+presidente\s+|apresentar\s+recurso\s+desta\s+decis[aã]o|Em\s+resposta\s+a\s*(?:o|ao)\s+Indeferi\s*-?\s*mento)"
        match = re.search(padrao, texto, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return texto[match.start():]
        else:
            fallbacks = [
                (r"^\s*RECURSO\s+CONTRA\s+INDEFERIMENTO\s*$", re.MULTILINE),
                (r"^\s*RECURSO\s+AO\s+INDEFERIMENTO\s*$", re.MULTILINE),
                (r"^\s*INTERPOSICAO\s+DE\s+RECURSO\s+AO\s+INDEFERIMENTO\s*$", re.MULTILINE),
                (r"(recurso\s+contra\s+o\s+indeferimento|recurso\s+ao\s+indeferimento|recurso\s+contra\s+decisao\s+de\s+indeferimento)", re.DOTALL),
                (r"(ilustrissimos\s+examinadores)", re.DOTALL),
                (r"(recurso\s+que\s+bastante\s+faz)", re.DOTALL)
            ]
            for f_padrao, f_flag in fallbacks:
                m = re.search(f_padrao, texto, flags=re.IGNORECASE | f_flag)
                if m: return texto[m.start():]
            return texto # Retorna texto total se não achar início específico

    # PIPELINE
    t = normalizar(texto)
    t = iniciar_apos_recurso(t)
    t = anonymize_remover_cabecalhos(t)
    t = anonymize_cpfs(t)
    t = anonymize_cnpj(t)
    t = anonymize_emails(t)
    t = anonymize_processos(t)
    t = anonymize_documentos_identificacao(t)
    t = anonymize_endereco(t)
    t = anonymize_telefone(t)
    t = anonymize_nomes_rotulados(t)
    t = anonymize_remover_linhas_com_cep(t)
    t = anonymize_remover_cabecalhos_pagina(t)
    return t

def ocr_simples(file_path, log_func):
    """Extração rápida de texto usando PyPDF2"""
    try:
        import PyPDF2
        log_func("Tentando PyPDF2...")
        with open(file_path, "rb") as file:
            reader = PyPDF2.PdfReader(file)
            txt = ""
            for p in reader.pages:
                extracted = p.extract_text()
                if extracted: txt += extracted
            
            if txt.strip():
                log_func("Extração PyPDF2 OK.")
                return txt.strip()
    except ImportError:
        log_func("PyPDF2 não instalado. Pulando...")
    except Exception as e:
        log_func(f"Falha PyPDF2: {e}")
    return ""

def ocr_and_markdown_file(instance_id):
    log_path = os.path.join(settings.MEDIA_ROOT, 'ocr_debug.log')
    def log(msg):
        with open(log_path, 'a', encoding='utf-8') as f:
            from datetime import datetime
            f.write(f"[{datetime.now()}] [V2.1] {msg}\n")
        print(f"[V2.1] {msg}")

    log(f"Iniciando tarefa para Documento ID: {instance_id}")
    try:
        documentos = get_object_or_404(Documentos, id=instance_id)
        file_path = documentos.arquivo.path
        
        texto_extraido = ""
        
        # Extração Rápida Protegida
        if file_path.lower().endswith('.pdf'):
            texto_extraido = ocr_simples(file_path, log)

        # Fallback Docling
        if not texto_extraido:
            log("Iniciando Docling (OCR Pesado)...")
            converter = DocumentConverter()
            result = converter.convert(file_path)
            texto_extraido = result.document.export_to_markdown()
            log("Docling OK.")
        
        if not texto_extraido:
            log("AVISO: Nenhuma extração retornou texto!")
            texto_extraido = "ERRO: Não foi possível extrair texto deste documento."

        # Regra de Anonimização
        tipos_preservados = ['6.1', '7.1', '9.2', '111', '121', '100.1', '100.2']
        if documentos.tipo in tipos_preservados:
            log(f"Tipo {documentos.tipo} detectado. Pulando anonimização.")
            texto_final = texto_extraido
        else:
            log("Processando anonimização...")
            texto_final = anonimizar_documento(texto_extraido)
            log("Anonimização OK.")
        
        # Salvamento
        documentos.content = texto_final
        documentos.save()
        log("Banco de dados atualizado.")

        txt_path = os.path.splitext(file_path)[0] + '.txt'
        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write(texto_final)
        log(f"Arquivo TXT gerado em: {os.path.basename(txt_path)}")
        
    except Exception as e:
        log(f"ERRO CRÍTICO: {e}")
        import traceback
        with open(log_path, 'a', encoding='utf-8') as f:
            traceback.print_exc(file=f)

def rag_documentos(instance_id):
    try:
        documentos = get_object_or_404(Documentos, id=instance_id)
        JuriAi.knowledge.insert(
            name=documentos.arquivo.name,
            text_content=documentos.content,
            metadata={'cliente_id': documentos.cliente.id}
        )
    except Exception as e:
        print(f"[RAG] Erro: {e}")
