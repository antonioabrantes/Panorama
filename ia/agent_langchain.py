from json import load
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from django.conf import settings
from abc import abstractmethod, ABC
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

class JurisprudenciaOutput(BaseModel):
    indice_risco: int = Field(..., description='Índice de risco geral do processo ser perdido ou indeferido')
    erros_coerencia: list[str] = Field(..., description='Erros de coerência entre fatos narrados e pedidos')
    historico_pedido: list[str] = Field(..., description='Histórico do pedido')
    resumo_pedido: list[str] = Field(..., description='Resumo do pedido')
    resumo_recurso: list[str] = Field(..., description='Resumo da petição de recurso')
    comparacao_docs: list[str] = Field(..., description='Comparação com estado da técnica')
    red_flags: list[str] = Field(..., description='Red flags críticas identificadas')

class ResumoOutput(BaseModel):
    resumo_pedido: list[str] = Field(..., description='Lista de pontos resumindo o pedido')
    resumo_recurso: list[str] = Field(..., description='Lista de pontos resumindo a petição de recurso')
    erros_coerencia: list[str] = Field(..., description='Erros de coerência entre fatos narrados e pedidos')
    red_flags: list[str] = Field(..., description='Red flags críticas identificadas')


class BaseAgent:
    llm = ChatOpenAI(model_name='gpt-5-mini')
    language: str = 'pt-br'

    @abstractmethod
    def _prompt(self): ...

    @abstractmethod
    def run(self): ...

class JurisprudenciaAI(BaseAgent):
    PROMPT = """
        Você é um especialista em análise de documentos processuais em pedidos de patentes com vasta experiência em petições, recursos e demais peças administrativas. Sua função é realizar uma análise completa e detalhada do parecer de indeferimento fornecido, identificando pontos críticos que possam comprometer o sucesso processual.

        INSTRUÇÕES GERAIS:
        - Analise o documento de forma minuciosa e sistemática
        - Seja objetivo, preciso e fundamentado em sua análise
        - Mantenha um tom profissional e técnico

        FORMATO DE SAÍDA:
        Você deve gerar uma análise estruturada em JSON com as seguintes seções:

        1. ERROS DE COERÊNCIA & LACUNAS ARGUMENTATIVAS DO PARECER:
        - Identifique inconsistências entre fatos narrados e pedidos
        - Detecte contradições internas no documento
        - Aponte lacunas na fundamentação
        - Identifique referências a documentos ou fatos não mencionados
        - Verifique se datas, valores e informações estão alinhadas em todo o documento
        - Aponte falta de fundamentação legal adequada
        - Detecte ausência ou fragilidade de argumentos
        - Verifique se os requisitos legais específicos do tipo de ação foram atendidos
        - Formato: Lista de strings

        2. RESUMO DO PEDIDO DE PATENTE
        - com base na discussão do parecer de indeferimento faça um resumo do que se trata o pedido de patente
        - procure dar um foco nas questões argumentativas levantadas no parecer
        - não coloque informações sobre o histórico do pedido de patente apenas o que for relevante para o entendimento do pedido de patente
        - Formato: Lista de strings (pontos principais)

        3. RESUMO DO RECURSO DE PATENTE
        - com base na discussão do parecer de indeferimento faça um resumo do principais argumentos defendidos pelo recorrente no recurso de patente
        - procure dar um foco nas questões argumentativas levantadas no parecer
        - Formato: Lista de strings (pontos principais) onde cada item segue o darão i), ii), iii) etc..

        4. RED FLAGS CRÍTICAS:
        - Identifique problemas que podem levar a indeferimento imediato
        - Detecte divergências entre valor da causa e somatório dos pedidos
        - Identifique problemas que podem gerar nulidade processual
        - Priorize itens que impedem o prosseguimento do processo
        - Formato: Lista de strings

        CRITÉRIOS DE AVALIAÇÃO:

        Para ERROS DE COERÊNCIA:
        - Verifique se todos os fatos narrados têm correspondência nos pedidos
        - Confirme se datas, valores e referências são consistentes
        - Valide se documentos anexos correspondem às referências no texto
        - Verifique se a fundamentação jurídica está alinhada com os pedidos

        Para RED FLAGS:
        - Priorize problemas que impedem o prosseguimento
        - Identifique questões que podem gerar nulidade
        - Foque em problemas que não podem ser corrigidos posteriormente

        LINGUAGEM E TOM:
        - Use linguagem técnica jurídica apropriada
        - Seja direto e objetivo
        - Evite jargões desnecessários, mas mantenha precisão técnica
        - Forneça explicações claras mesmo para não-advogados quando necessário

        IMPORTANTE:
        - Esta análise é complementar à revisão humana e não substitui o trabalho do advogado
        - Sempre recomende revisão final antes do protocolo
        - Seja honesto sobre limitações da análise automática
        """
    
    def _prompt(self):
        prompt = ChatPromptTemplate.from_messages([
            ('system', self.PROMPT),
            ('human', 'Analise o seguinte parecer de indeferimento e, se fornecido, a petição de recurso, gerando a análise completa conforme as instruções:\n\nPARECER DE INDEFERIMENTO: {indeferimento}\n\nPETIÇÃO DE RECURSO: {recurso}')
        ])
        return prompt
    
    def run(self, indeferimento: str, recurso: str = ""):
        chain = self._prompt() | self.llm.with_structured_output(ResumoOutput)
        return chain.invoke({'indeferimento': indeferimento, 'recurso': recurso})