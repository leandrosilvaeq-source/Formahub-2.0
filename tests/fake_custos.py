"""Repositório de custos em memória, para os testes. Nada acessa o Supabase.

Imita as garantias das funções do banco: usuário válido (PT403), chave existente (PT404),
valores inválidos (PT422), nulo só onde a migration permite (manutenção e ajustes *_manual) e
residual <= aquisição. Os valores iniciais são os da migration 20261019120000_custos_gestao.
Falhas de comunicação podem ser simuladas com `falhar_em`.
"""

from datetime import date
from decimal import Decimal

from app.custos import (
    AQUISICAO,
    CONSUMO_KWH,
    DEPRECIACAO_MANUAL,
    ENERGIA_MANUAL,
    HORAS_MES,
    LIMITE_VALOR,
    MANUTENCAO,
    MDO,
    MDO_DIAS_MES,
    MDO_HORAS_DIA,
    MDO_REVISOES,
    MEI,
    PERDA,
    RESIDUAL,
    RETIRADA,
    TARIFA,
    TAXA_FIXA,
    TAXA_PERCENTUAL,
    VIDA_UTIL,
)
from app.custos_repositorio import (
    CustoRecusado,
    Custos,
    GastoManutencao,
    PeriodoManutencao,
)

USUARIOS = {1: "Leandro", 2: "Kassia", 3: "Marise"}
ANULAVEIS = {MANUTENCAO, ENERGIA_MANUAL, DEPRECIACAO_MANUAL}  # NULL = voltar ao calculado
CHAVES = {
    CONSUMO_KWH,
    HORAS_MES,
    TARIFA,
    PERDA,
    MDO,
    AQUISICAO,
    VIDA_UTIL,
    RESIDUAL,
    TAXA_FIXA,
    TAXA_PERCENTUAL,
    MEI,
    RETIRADA,
    MDO_HORAS_DIA,
    MDO_DIAS_MES,
    MDO_REVISOES,
    *ANULAVEIS,
}
DATA_BASE = date(2026, 11, 1)


class RepositorioCustosMemoria:
    def __init__(self):
        self.parametros = {
            CONSUMO_KWH: Decimal("40"),
            HORAS_MES: Decimal("305"),
            TARIFA: Decimal("1.0393"),
            PERDA: Decimal("3"),
            MDO: Decimal("15"),
            AQUISICAO: Decimal("5000"),
            VIDA_UTIL: Decimal("5"),
            RESIDUAL: Decimal("0"),
            TAXA_FIXA: Decimal("0"),
            TAXA_PERCENTUAL: Decimal("0"),
            MEI: Decimal("100"),
            RETIRADA: Decimal("500"),
            MDO_HORAS_DIA: Decimal("4"),
            MDO_DIAS_MES: Decimal("30"),
            MDO_REVISOES: Decimal("0"),
            ENERGIA_MANUAL: Decimal("0.14"),  # ajuste manual provisório da migration
            DEPRECIACAO_MANUAL: Decimal("0.27"),
            MANUTENCAO: None,  # sem ajuste e, sem gastos, "A definir"
        }
        self.provisorios = {ENERGIA_MANUAL, DEPRECIACAO_MANUAL}
        self.datas = {HORAS_MES: DATA_BASE}
        self.ajustes: dict[str, Decimal] = {}
        # Os gastos vêm dos registros da tela Manutenção (aqui, preenchidos pelo teste).
        self.gastos: list[GastoManutencao] = []
        self.manutencoes_sem_custo: tuple[date, ...] = ()
        self.periodo: PeriodoManutencao | None = None
        self.proximo_id = 1
        self.falhar_em: dict[str, Exception] = {}  # operação -> erro a levantar
        # Cada gravação aceita: (operação, argumentos..., usuário).
        self.gravacoes: list[tuple] = []
        self.leituras = 0

    def _falhar(self, operacao: str):
        if operacao in self.falhar_em:
            raise self.falhar_em[operacao]

    def _usuario(self, usuario_id: int):
        if usuario_id not in USUARIOS:
            raise CustoRecusado("usuario")

    def listar(self) -> Custos:
        self._falhar("listar")
        self.leituras += 1
        return Custos(
            parametros=dict(self.parametros),
            provisorios=frozenset(self.provisorios),
            datas=dict(self.datas),
            ajustes=dict(self.ajustes),
            gastos=sorted(self.gastos, key=lambda g: (g.data, g.id), reverse=True),
            periodo=self.periodo,
            manutencoes_sem_custo=self.manutencoes_sem_custo,
        )

    def salvar_parametros(
        self, valores: dict[str, Decimal | None], usuario_id: int
    ) -> dict[str, Decimal | None]:
        self._falhar("salvar_parametros")
        self._usuario(usuario_id)
        if not valores:
            raise CustoRecusado("invalido")
        for chave, valor in valores.items():
            if chave not in CHAVES:
                raise CustoRecusado("nao_encontrado")
            if valor is None:  # só a manutenção e os ajustes manuais voltam a "sem valor"
                if chave not in ANULAVEIS:
                    raise CustoRecusado("invalido")
                continue
            if not (Decimal(0) <= valor < LIMITE_VALOR):
                raise CustoRecusado("invalido")
            if (
                (chave in (PERDA, TAXA_PERCENTUAL) and valor > 100)
                or (chave == VIDA_UTIL and valor <= 0)
                or (chave == HORAS_MES and not (0 < valor <= 744))
                or (chave == MDO_HORAS_DIA and not (0 < valor <= 24))
                or (chave == MDO_DIAS_MES and not (1 <= valor <= 31 and valor == int(valor)))
                or (chave == MDO_REVISOES and valor != int(valor))
            ):
                raise CustoRecusado("invalido")
        final = {**self.parametros, **valores}  # valida o estado final antes de gravar
        if final[RESIDUAL] > final[AQUISICAO]:
            raise CustoRecusado("invalido")
        self.parametros = final  # só depois de validar tudo: tudo ou nada
        self.provisorios -= set(valores)  # qualquer gravação pelo app desmarca o "provisório"
        self.datas.update({chave: date.today() for chave in valores})
        self.gravacoes.append(("parametros", dict(valores), usuario_id))
        return dict(self.parametros)

    def salvar_ajuste_insumo(self, chave: str, valor: Decimal | None, usuario_id: int) -> None:
        self._falhar("salvar_ajuste_insumo")
        self._usuario(usuario_id)
        if not chave.partition(":")[2] or chave.partition(":")[0] not in (
            "filamento",
            "acessorio",
            "embalagem",
        ):
            raise CustoRecusado("invalido")
        if valor is None:
            self.ajustes.pop(chave, None)
        elif Decimal(0) <= valor < LIMITE_VALOR:
            self.ajustes[chave] = valor
        else:
            raise CustoRecusado("invalido")
        self.gravacoes.append(("ajuste", chave, valor, usuario_id))

    def definir_periodo_manutencao(
        self, inicio: date, fim: date, horas: Decimal, usuario_id: int
    ) -> PeriodoManutencao:
        self._falhar("definir_periodo_manutencao")
        self._usuario(usuario_id)
        if fim < inicio or horas <= 0:
            raise CustoRecusado("invalido")
        self.periodo = PeriodoManutencao(inicio, fim, horas)
        self.gravacoes.append(("periodo", inicio, fim, horas, usuario_id))
        return self.periodo
