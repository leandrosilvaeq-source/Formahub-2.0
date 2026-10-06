"""Repositório de custos em memória, para os testes. Nada acessa o Supabase.

Imita as garantias das funções do banco: usuário válido (PT403), registro existente (PT404),
nome repetido no mesmo tipo (PT409), valores inválidos (PT422) e custo unitário exato
(valor / quantidade, sem arredondar). Os registros iniciais são os da migration.
Falhas de comunicação podem ser simuladas com `falhar_em`.
"""

from decimal import Decimal

from app.custos import (
    AQUISICAO,
    CONSUMO,
    DIAS_MES,
    HORAS_DIA,
    LIMITE_VALOR,
    MANUTENCAO,
    MDO,
    PERDA,
    RESIDUAL,
    TARIFA,
    VIDA_UTIL,
    custo_unitario,
)
from app.custos_repositorio import (
    CustoRecusado,
    Custos,
    Filamento,
    ItemCusto,
)

USUARIOS = {1: "Leandro", 2: "Kassia", 3: "Marise"}
CHAVES = {
    TARIFA,
    CONSUMO,
    PERDA,
    MDO,
    MANUTENCAO,
    AQUISICAO,
    VIDA_UTIL,
    RESIDUAL,
    HORAS_DIA,
    DIAS_MES,
}


class RepositorioCustosMemoria:
    def __init__(self):
        self.filamentos = {
            1: Filamento(1, "PLA", "Cores sólidas, Velvet, Silk e pastel", Decimal("100")),
            2: Filamento(2, "PLA Multicolor", "Duocolor e tricolor", Decimal("125")),
            3: Filamento(3, "PETG", None, Decimal("85")),
        }
        self.parametros = {
            TARIFA: Decimal("1"),
            CONSUMO: Decimal("120"),
            PERDA: Decimal("5"),
            MDO: Decimal("0.5"),
            MANUTENCAO: None,  # "A definir": ausência de valor, não zero
            AQUISICAO: Decimal("6000"),
            VIDA_UTIL: Decimal("5"),
            RESIDUAL: Decimal("0"),
            HORAS_DIA: Decimal("12"),
            DIAS_MES: Decimal("30"),
        }
        self.itens: dict[int, ItemCusto] = {}
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
            filamentos=sorted(self.filamentos.values(), key=lambda f: f.id),
            parametros=dict(self.parametros),
            itens=sorted(self.itens.values(), key=lambda i: (i.nome.lower(), i.id)),
        )

    def salvar_filamento(self, filamento_id: int, valor_kg: Decimal, usuario_id: int) -> Filamento:
        self._falhar("salvar_filamento")
        self._usuario(usuario_id)
        if not (Decimal(0) <= valor_kg < LIMITE_VALOR):
            raise CustoRecusado("invalido")
        if filamento_id not in self.filamentos:
            raise CustoRecusado("nao_encontrado")
        atual = self.filamentos[filamento_id]
        self.filamentos[filamento_id] = Filamento(atual.id, atual.nome, atual.abrange, valor_kg)
        self.gravacoes.append(("filamento", filamento_id, valor_kg, usuario_id))
        return self.filamentos[filamento_id]

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
            if valor is None:  # só a manutenção pode ficar sem valor
                if chave != MANUTENCAO:
                    raise CustoRecusado("invalido")
                continue
            if not (Decimal(0) <= valor < LIMITE_VALOR) or (chave == PERDA and valor > 100):
                raise CustoRecusado("invalido")
            if (
                (chave == VIDA_UTIL and valor <= 0)
                or (
                    chave == DIAS_MES
                    and not (1 <= valor <= 31 and valor == valor.to_integral_value())
                )
                or (chave == HORAS_DIA and not (0 < valor <= 24))
            ):
                raise CustoRecusado("invalido")
        final = {**self.parametros, **valores}  # valida o estado final antes de gravar
        if final[RESIDUAL] > final[AQUISICAO]:
            raise CustoRecusado("invalido")
        self.parametros = final  # só depois de validar tudo: tudo ou nada
        self.gravacoes.append(("parametros", dict(valores), usuario_id))
        return dict(self.parametros)

    def salvar_item(
        self,
        item_id: int | None,
        tipo: str,
        nome: str,
        valor_compra: Decimal,
        quantidade: int,
        usuario_id: int,
    ) -> ItemCusto:
        self._falhar("salvar_item")
        self._usuario(usuario_id)
        nome = nome.strip()
        if (
            tipo not in ("acessorio", "embalagem")
            or not nome
            or len(nome) > 120
            or not (Decimal(0) <= valor_compra < LIMITE_VALOR)
            or quantidade <= 0
        ):
            raise CustoRecusado("invalido")
        if item_id is not None and (item_id not in self.itens or self.itens[item_id].tipo != tipo):
            raise CustoRecusado("nao_encontrado")
        if any(
            i.tipo == tipo and i.nome.lower() == nome.lower() and i.id != item_id
            for i in self.itens.values()
        ):
            raise CustoRecusado("duplicado")
        if item_id is None:
            item_id = self.proximo_id
            self.proximo_id += 1
        item = ItemCusto(
            item_id, tipo, nome, valor_compra, quantidade, custo_unitario(valor_compra, quantidade)
        )
        self.itens[item_id] = item
        self.gravacoes.append(("item", item_id, tipo, nome, valor_compra, quantidade, usuario_id))
        return item
