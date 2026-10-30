"""Leitura fiscal integrada do projeto MagroNego/Leitor_XML_Saida (v2.0)."""
from defusedxml import ElementTree as ET
import re
from decimal import Decimal, InvalidOperation


# ─────────────────────────────────────────────────────────────────────────────
#  FORMATAÇÃO
# ─────────────────────────────────────────────────────────────────────────────

def format_cnpj(cnpj):
    cnpj = re.sub(r'\D', '', str(cnpj or ''))
    if len(cnpj) == 14:
        return f"{cnpj[0:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:14]}"
    return cnpj

def format_chave(chave):
    chave = re.sub(r'\D', '', str(chave or ''))
    if len(chave) == 44:
        return ' '.join(chave[i:i+4] for i in range(0, 44, 4))
    return chave

def decimal_value(v):
    try:
        n = Decimal(str(v).replace(',', '.'))
        if n.is_finite() and (abs(n.as_tuple().exponent) > 100 or abs(n.adjusted()) > 30 or len(n.as_tuple().digits) > 100):
            raise ValueError("Valor numérico fora dos limites da NF-e.")
        return n if n.is_finite() else Decimal('0')
    except (InvalidOperation, TypeError):
        return Decimal('0')

def fmt_valor(v):
    if v is None or v == '': return ''
    return format(decimal_value(v), '.2f').replace('.', ',')

def fmt_qtd(v):
    if v is None or v == '': return ''
    return format(decimal_value(v), 'f').rstrip('0').rstrip('.').replace('.', ',') if '.' in format(decimal_value(v), 'f') else format(decimal_value(v), 'f')

def fmt_perc(v):
    return fmt_qtd(v)

def zfill_cst(v, width=2):
    """Garante zero à esquerda: '1'→'01', '8'→'08', '51'→'51'."""
    if not v:
        return ''
    v = str(v).strip()
    return v.zfill(width) if len(v) < width else v


# ─────────────────────────────────────────────────────────────────────────────
#  PARSER NF-e  — retorna (lista_itens, dict_retido)
# ─────────────────────────────────────────────────────────────────────────────

def parse_nfe_xml(xml_file_path, *, root=None):
    if root is None:
        root = ET.parse(xml_file_path).getroot()

    ns_match = re.match(r'\{(.+)\}', root.tag)
    ns     = {'n': ns_match.group(1)} if ns_match else {}
    has_ns = bool(ns)

    def find(parent, tag):
        if parent is None:
            return None
        return parent.find(f'n:{tag}', ns) if has_ns else parent.find(tag)

    def findall(parent, tag):
        if parent is None:
            return []
        return parent.findall(f'n:{tag}', ns) if has_ns else parent.findall(tag)

    def txt(parent, tag, default=''):
        if parent is None:
            return default
        elem = find(parent, tag)
        return elem.text.strip() if elem is not None and elem.text else default

    nfe = find(root, 'NFe')
    if nfe is None:
        nfe = root
    infNFe = find(nfe, 'infNFe')
    if infNFe is None:
        return None, None

    ide  = find(infNFe, 'ide')
    emit = find(infNFe, 'emit')
    dest = find(infNFe, 'dest')

    chnfe        = format_chave(infNFe.get('Id', '').replace('NFe', ''))
    numero_nota  = txt(ide,  'nNF')
    serie        = txt(ide,  'serie')
    # dhEmi formato: 2026-06-03T09:50:06-03:00  →  separa data e hora
    _dhEmi = txt(ide, 'dhEmi') or txt(ide, 'dEmi')
    if 'T' in _dhEmi:
        _d, _t   = _dhEmi.split('T', 1)
        # remove timezone (+/-HH:MM ou Z)
        _t_clean = re.split(r'[+-]', _t)[0] if re.search(r'[+-]', _t) else _t.rstrip('Z')
        _d_parts = _d.split('-')
        data_emissao = f"{_d_parts[2]}/{_d_parts[1]}/{_d_parts[0]}" if len(_d_parts) == 3 else _d
        hora_emissao = _t_clean
    else:
        _d_parts = _dhEmi.split('-')
        data_emissao = f"{_d_parts[2]}/{_d_parts[1]}/{_d_parts[0]}" if len(_d_parts) == 3 else _dhEmi
        hora_emissao = ''
    emit_cnpj    = format_cnpj(txt(emit, 'CNPJ'))
    emit_razao   = txt(emit, 'xNome')
    dest_cnpj    = format_cnpj(txt(dest, 'CNPJ') or txt(dest, 'CPF'))
    dest_razao   = txt(dest, 'xNome')
    enderDest    = find(dest, 'enderDest')
    dest_uf      = txt(enderDest, 'UF') if enderDest is not None else ''

    # ── PIS / COFINS Retido — nível nota ─────────────────────────────────────
    total_elem = find(infNFe, 'total')
    ret_trib   = find(total_elem, 'retTrib') if total_elem is not None else None
    vRetPIS    = fmt_valor(txt(ret_trib, 'vRetPIS'))
    vRetCOFINS = fmt_valor(txt(ret_trib, 'vRetCOFINS'))

    # Só gera linha na planilha de retidos se houver valor
    retido = None
    if decimal_value(txt(ret_trib, 'vRetPIS')) > 0 or decimal_value(txt(ret_trib, 'vRetCOFINS')) > 0:
        retido = {
            'Chave Acesso':       chnfe,
            'Numero Nota':        numero_nota,
            'Serie':              serie,
            'Data Emissao':       data_emissao,
            'Hora Emissao':       hora_emissao,
            'Emitente CNPJ':      emit_cnpj,
            'Emitente Razao':     emit_razao,
            'Destinatario CNPJ':  dest_cnpj,
            'Destinatario Razao': dest_razao,
            'Vl. PIS Retido':     vRetPIS,
            'Vl. COFINS Retido':  vRetCOFINS,
        }

    # ── Itens ─────────────────────────────────────────────────────────────────
    itens = []

    for det in findall(infNFe, 'det'):
        n_item = det.get('nItem', '')
        prod   = find(det, 'prod')
        imp    = find(det, 'imposto')

        cProd  = txt(prod, 'cProd')
        xProd  = txt(prod, 'xProd')
        NCM    = txt(prod, 'NCM')
        CFOP   = txt(prod, 'CFOP')
        uCom   = txt(prod, 'uCom')
        qCom   = fmt_qtd(txt(prod, 'qCom'))
        vUnCom = fmt_qtd(txt(prod, 'vUnCom'))
        vProd  = fmt_valor(txt(prod, 'vProd'))

        # ICMS
        orig_icms = cst_icms = csosn = vBC_icms = pICMS = vICMS = ''
        icms_grp = find(imp, 'ICMS')
        if icms_grp is not None:
            for child in icms_grp:
                tag_name = child.tag.split('}')[-1] if '}' in child.tag else child.tag
                if tag_name.startswith('ICMS'):
                    orig_icms = txt(child, 'orig')
                    cst_icms  = zfill_cst(txt(child, 'CST'), 2)
                    csosn = txt(child, 'CSOSN')
                    vBC_icms  = fmt_valor(txt(child, 'vBC'))
                    pICMS     = fmt_perc(txt(child, 'pICMS'))
                    vICMS     = fmt_valor(txt(child, 'vICMS'))
                    break

        # IPI
        cst_ipi = vBC_ipi = pIPI = vIPI = ''
        ipi_grp = find(imp, 'IPI')
        if ipi_grp is not None:
            ipint   = find(ipi_grp, 'IPINT')
            ipitrib = find(ipi_grp, 'IPITrib')
            if ipint is not None:
                cst_ipi = zfill_cst(txt(ipint, 'CST'), 2)
            elif ipitrib is not None:
                cst_ipi = zfill_cst(txt(ipitrib, 'CST'), 2)
                vBC_ipi = fmt_valor(txt(ipitrib, 'vBC'))
                pIPI    = fmt_perc(txt(ipitrib, 'pIPI'))
                vIPI    = fmt_valor(txt(ipitrib, 'vIPI'))

        # PIS
        cst_pis = vBC_pis = pPIS = vPIS = ''
        pis_grp = find(imp, 'PIS')
        if pis_grp is not None:
            pisaliq = find(pis_grp, 'PISAliq')
            pisnt   = find(pis_grp, 'PISNT')
            pisoutr = find(pis_grp, 'PISOutr')
            pisqt   = find(pis_grp, 'PISQtde')
            if pisaliq is not None:
                cst_pis = zfill_cst(txt(pisaliq, 'CST'), 2)
                vBC_pis = fmt_valor(txt(pisaliq, 'vBC'))
                pPIS    = fmt_perc(txt(pisaliq, 'pPIS'))
                vPIS    = fmt_valor(txt(pisaliq, 'vPIS'))
            elif pisnt is not None:
                cst_pis = zfill_cst(txt(pisnt, 'CST'), 2)
            elif pisoutr is not None:
                cst_pis = zfill_cst(txt(pisoutr, 'CST'), 2)
                vBC_pis = fmt_valor(txt(pisoutr, 'vBC'))
                pPIS    = fmt_perc(txt(pisoutr, 'pPIS'))
                vPIS    = fmt_valor(txt(pisoutr, 'vPIS'))
            elif pisqt is not None:
                cst_pis = zfill_cst(txt(pisqt, 'CST'), 2)
                vBC_pis = ''
                vPIS    = fmt_valor(txt(pisqt, 'vPIS'))

        # COFINS
        cst_cofins = vBC_cofins = pCOFINS = vCOFINS = ''
        cof_grp = find(imp, 'COFINS')
        if cof_grp is not None:
            cofaliq = find(cof_grp, 'COFINSAliq')
            cofnt   = find(cof_grp, 'COFINSNT')
            cofoutr = find(cof_grp, 'COFINSOutr')
            cofqt   = find(cof_grp, 'COFINSQtde')
            if cofaliq is not None:
                cst_cofins = zfill_cst(txt(cofaliq, 'CST'), 2)
                vBC_cofins = fmt_valor(txt(cofaliq, 'vBC'))
                pCOFINS    = fmt_perc(txt(cofaliq, 'pCOFINS'))
                vCOFINS    = fmt_valor(txt(cofaliq, 'vCOFINS'))
            elif cofnt is not None:
                cst_cofins = zfill_cst(txt(cofnt, 'CST'), 2)
            elif cofoutr is not None:
                cst_cofins = zfill_cst(txt(cofoutr, 'CST'), 2)
                vBC_cofins = fmt_valor(txt(cofoutr, 'vBC'))
                pCOFINS    = fmt_perc(txt(cofoutr, 'pCOFINS'))
                vCOFINS    = fmt_valor(txt(cofoutr, 'vCOFINS'))
            elif cofqt is not None:
                cst_cofins = zfill_cst(txt(cofqt, 'CST'), 2)
                vBC_cofins = ''
                vCOFINS    = fmt_valor(txt(cofqt, 'vCOFINS'))

        itens.append({
            'Chave Acesso':        chnfe,
            'Numero Nota':         numero_nota,
            'Serie':               serie,
            'Data Emissao':        data_emissao,
            'Emitente CNPJ':       emit_cnpj,
            'Emitente Razao':      emit_razao,
            'Destinatario CNPJ':   dest_cnpj,
            'Destinatario Razao':  dest_razao,
            'Destinatario UF':     dest_uf,
            'Item':                n_item,
            'Codigo':              cProd,
            'Descricao':           xProd,
            'NCM':                 NCM,
            'CFOP':                CFOP,
            'Unidade':             uCom,
            'Qtd':                 qCom,
            'Vl. Unitario':        vUnCom,
            'Vl. Total':           vProd,
            'CST ICMS':            orig_icms + cst_icms if cst_icms else '',
            'CSOSN': csosn,
            'Origem ICMS': orig_icms,
            'Qtd Base PIS': fmt_qtd(txt(find(pis_grp, 'PISQtde'), 'qBCProd')),
            'Aliquota PIS por unidade': fmt_qtd(txt(find(pis_grp, 'PISQtde'), 'vAliqProd')),
            'Qtd Base COFINS': fmt_qtd(txt(find(cof_grp, 'COFINSQtde'), 'qBCProd')),
            'Aliquota COFINS por unidade': fmt_qtd(txt(find(cof_grp, 'COFINSQtde'), 'vAliqProd')),
            'Base ICMS':           vBC_icms,
            '% ICMS':              pICMS,
            'Vl. ICMS':            vICMS,
            'CST IPI':             cst_ipi,
            'Base IPI':            vBC_ipi,
            '% IPI':               pIPI,
            'Vl. IPI':             vIPI,
            'CST PIS':             cst_pis,
            'Base PIS':            vBC_pis,
            '% PIS':               pPIS,
            'Vl. PIS':             vPIS,
            'CST COFINS':          cst_cofins,
            'Base COFINS':         vBC_cofins,
            '% COFINS':            pCOFINS,
            'Vl. COFINS':          vCOFINS,
        })

    return itens, retido


# ─────────────────────────────────────────────────────────────────────────────
#  COLUNAS
# ─────────────────────────────────────────────────────────────────────────────

FIELDS_ITENS = [
    'Chave Acesso', 'Numero Nota', 'Serie', 'Data Emissao',
    'Emitente CNPJ', 'Emitente Razao',
    'Destinatario CNPJ', 'Destinatario Razao', 'Destinatario UF',
    'Item', 'Codigo', 'Descricao', 'NCM', 'CFOP', 'Unidade', 'Qtd',
    'Vl. Unitario', 'Vl. Total',
    'CST ICMS',   'Base ICMS',   '% ICMS',   'Vl. ICMS',
    'CST IPI',    'Base IPI',    '% IPI',    'Vl. IPI',
    'CST PIS',    'Base PIS',    '% PIS',    'Vl. PIS',
    'CST COFINS', 'Base COFINS', '% COFINS', 'Vl. COFINS',
]

FIELDS_RETIDO = [
    'Chave Acesso', 'Numero Nota', 'Serie', 'Data Emissao', 'Hora Emissao',
    'Emitente CNPJ', 'Emitente Razao',
    'Destinatario CNPJ', 'Destinatario Razao',
    'Vl. PIS Retido', 'Vl. COFINS Retido',
]



FIELDS_ITENS += ['CSOSN', 'Origem ICMS', 'Qtd Base PIS', 'Aliquota PIS por unidade', 'Qtd Base COFINS', 'Aliquota COFINS por unidade']
