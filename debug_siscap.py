import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from usuarios.models import Cliente, Documentos

client = Cliente.objects.get(id=1)
numero_para_teste = client.numero if client.numero else client.nome
print(f"Cliente 1: {client.nome}, Numero real: '{client.numero}', Numero para teste: '{numero_para_teste}'")

docs = Documentos.objects.filter(cliente=client, tipo='9.2')
print(f"Documentos 9.2 para o cliente 1: {docs.count()}")
for doc in docs:
    print(f"Documento ID {doc.id}, Tipo: {doc.tipo}")
    # Forçamos o numero no cliente do documento para o teste
    if not doc.cliente.numero:
        doc.cliente.numero = numero_para_teste
    link = doc.get_siscap_link()
    print(f"Link gerado: {link}")
