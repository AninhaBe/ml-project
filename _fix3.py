import dataclasses, json

content = open('app.py', encoding='utf-8').read()

# 1. Adiciona imports das novas funções de cache
old1 = 'from src.cache import pdf_produtos_lookup, pdf_produtos_save'
new1 = 'from src.cache import pdf_produtos_lookup, pdf_produtos_save, pdf_resultados_lookup, pdf_resultados_save'
assert old1 in content, "Import nao encontrado"
content = content.replace(old1, new1)

# 2. Antes de processar lote, tenta carregar do cache de resultados
# e após concluir, salva no cache
old2 = '''        if produtos:
            progress = st.progress(0.0, text="Analisando produtos...")
            pipeline = get_pipeline()
            resultados_pdf: list[ResultadoProduto] = []

            def cb(i: int, total: int, p: ProdutoFornecedor, r: ResultadoProduto) -> None:
                resultados_pdf.append(r)
                progress.progress(i / total, text=f"[{i}/{total}] {p.nome[:60]}")

            pipeline.processar_lote(produtos, progress_callback=cb)
            st.session_state.resultados.extend(resultados_pdf)
            progress.empty()
            st.rerun()'''

new2 = '''        if produtos:
            cached_res = pdf_resultados_lookup(pdf_path)
            if cached_res is not None:
                resultados_pdf = _reconstruir_resultados(cached_res)
                st.info(f"\u26a1 Cache: {len(resultados_pdf)} resultados carregados instantaneamente.")
                st.session_state.resultados.extend(resultados_pdf)
                st.rerun()
            else:
                progress = st.progress(0.0, text="Analisando produtos...")
                pipeline = get_pipeline()
                resultados_pdf: list[ResultadoProduto] = []

                def cb(i: int, total: int, p: ProdutoFornecedor, r: ResultadoProduto) -> None:
                    resultados_pdf.append(r)
                    progress.progress(i / total, text=f"[{i}/{total}] {p.nome[:60]}")

                pipeline.processar_lote(produtos, progress_callback=cb)
                pdf_resultados_save(pdf_path, [_serializar_resultado(r) for r in resultados_pdf])
                st.session_state.resultados.extend(resultados_pdf)
                progress.empty()
                st.rerun()'''

assert old2 in content, "Bloco processar_lote nao encontrado"
content = content.replace(old2, new2)

# 3. Adiciona funções helper de serialização após os imports
helper = '''

def _serializar_resultado(r: "ResultadoProduto") -> dict:
    import dataclasses
    def _conv(obj):
        if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
            return {k: _conv(v) for k, v in dataclasses.asdict(obj).items()}
        if isinstance(obj, list):
            return [_conv(i) for i in obj]
        if isinstance(obj, dict):
            return {k: _conv(v) for k, v in obj.items()}
        return obj
    return _conv(r)


def _reconstruir_resultados(dados: list[dict]) -> list["ResultadoProduto"]:
    from src.matcher import MatchResult
    from src.analyzer import AnaliseConcorrencia, AnuncioConcorrente
    from src.exporter import LinhaResultado
    resultados = []
    for d in dados:
        p = d["produto"]
        produto = ProdutoFornecedor(
            nome=p["nome"], preco=p["preco"], codigo=p["codigo"],
            marca_extraida=p["marca_extraida"], pagina=p["pagina"], raw_text=p["raw_text"],
        )
        m = d["match"]
        match = MatchResult(
            catalog_product_id=m["catalog_product_id"],
            confianca=m["confianca"], metodo=m["metodo"],
            candidato_ml=m["candidato_ml"], motivo=m["motivo"],
        )
        le = d["linha_excel"]
        linha = LinhaResultado(**le)
        resultados.append(ResultadoProduto(produto=produto, match=match, analise=None, linha_excel=linha))
    return resultados

'''

# Insere após os imports (após a linha do Pipeline import)
insert_after = 'from src.pipeline import Pipeline, ResultadoProduto'
assert insert_after in content, "Insert point nao encontrado"
content = content.replace(insert_after, insert_after + helper, 1)

open('app.py', 'w', encoding='utf-8').write(content)
print("OK")
