from django.db import models
from django.contrib.auth.models import User
from martor.models import MartorField
import requests
import json
import urllib.parse


class Cliente(models.Model):
    TIPO_CHOICES = [
        ('PF', 'Pessoa fisica'),
        ('PJ', 'Pessoa juridica'),
    ]

    nome = models.CharField(max_length=255)
    email = models.EmailField()
    numero = models.CharField(max_length=50, blank=True, null=True)
    tipo = models.CharField(max_length=2, choices=TIPO_CHOICES, default='PF')
    status = models.BooleanField(default=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE)

    def __str__(self):
        return self.nome
    
class Documentos(models.Model):
    TIPO_CHOICES = [
        ('6.1', 'exigência (6.1)'),
        ('7.1', 'ciência de parecer (7.1)'),
        ('200', 'depósito (200)'),
        ('203', 'exame (203)'),
        ('207', 'cumprimento exigência (207)'),
        ('260', 'outras petições (260)'),
        ('281', 'manifestação (281)'),
        ('9.2', 'indeferimento (9.2)'),
        ('214', 'recurso (214)'),
        ('121', 'recurso exigência (121)'),
        ('280', 'cumprimento exigência (280)'),
        ('111', 'recurso negado (111)'),
        ('100.1', 'recurso provido-reforma (100.1)'),
        ('100.2', 'recurso provido-devolução (100.2)'),
        ('130', 'recurso prejudicado (130)'),
    ]
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE)
    tipo = models.CharField(max_length=255, choices=TIPO_CHOICES, default='O')
    codigo = models.IntegerField(blank=True, null=True)
    arquivo = models.FileField(upload_to='documentos/')
    data_upload = models.DateTimeField()
    content = MartorField()
    numnossonumero = models.CharField(max_length=25, blank=True, null=True)
    peticao = models.CharField(max_length=255, default='')
    documento_pdf = models.CharField(max_length=255, default='')

    def __str__(self):
        return self.tipo

    def get_siscap_link(self):
        # Fallback para o campo 'nome' se o 'numero' estiver vazio, 
        # já que muitos clientes existentes usam o nome como número do processo.
        numero = self.cliente.numero if self.cliente.numero else self.cliente.nome
        
        if self.tipo != '9.2' or not numero:
            return None

        try:
            query_str = f" * FROM pedido where decisao in ('indeferimento','9.2') and numero='{numero}'"
            query_json = json.dumps({"mysql_query": query_str})
            
            # Codifica a query para URL
            import urllib.parse
            encoded_query = urllib.parse.quote(query_json)
            url = f"https://cientistaspatentes.com.br/apiphp/patents/query/?q={encoded_query}"
            
            headers = {
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            }
            
            # Verificação desabilitada conforme solicitado (verify=False)
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            
            response = requests.get(url, headers=headers, verify=False, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if "patents" in data and len(data["patents"]) > 0:
                    codigo = data["patents"][0]["codigo"]
                    divisao = data["patents"][0]["divisao"]
                    return f"http://siscap.inpi.gov.br/adm/pareceres/{divisao}/00_{numero}{codigo}.pdf"
        except Exception as e:
            # Mantendo apenas um print simples para erros reais de conexão/API
            print(f"Erro ao buscar link do Siscap para o documento {self.id}: {e}")
        
        return None

    def get_pdf_link(self):
        if self.documento_pdf:
            return self.documento_pdf
        link = self.get_siscap_link()
        if link:
            return link
        return self.arquivo.url if self.arquivo else None

    def get_txt_link(self):
        pdf_link = self.get_pdf_link()
        if pdf_link:
            import re
            return re.sub(r'\.pdf$', '.txt', pdf_link, flags=re.IGNORECASE)
        return None

    @property
    def is_urgente(self):
        # Tipos que requerem destaque visual e pulam anonimização
        tipos_urgentes = ['6.1', '7.1', '9.2', '111', '121', '100.1', '100.2']
        return self.tipo in tipos_urgentes