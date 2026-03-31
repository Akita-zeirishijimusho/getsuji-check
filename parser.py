# -*- coding: utf-8 -*-
"""
ICS出力Excelファイルのパーサー
月次試算表・比較損益推移表を読み込み、チェック用データ構造に変換する
"""
import io
import re
import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple, Any


# 月の番号に対応するキーワード
MONTH_PATTERN = re.compile(r'(\d{1,2})月')

# チェック対象の主要科目キーワード（部分一致）
KEY_ACCOUNTS = [
    '現金', '当座預金', '普通預金', '定期預金',
    '売掛金', '受取手形', '未収金', '前払費用', '前払金', '立替金',
    '仮払金', '仮受金',
    '商品', '製品', '原材料', '仕掛品',
    '建物', '構築物', '機械装置', '車両運搬具', '器具備品', '工具', '土地',
    '長期前払費用', '敷金', '保証金',
    '短期借入金', '長期借入金', '買掛金', '支払手形', '未払金', '未払費用',
    '資本金', '利益剰余金',
    '売上高', '売上', '仕入高', '仕入',
    '外注費', '給料手当', '給与', '役員報酬',
    '福利厚生費', '旅費交通費', '通信費', '水道光熱費',
    '消耗品費', '修繕費', '地代家賃', '賃借料',
    '租税公課', '保険料', '減価償却費',
    '広告宣伝費', '接待交際費', '会議費',
    '雑費', '雑収入', '雑損失', '雑支出',
]


def _clean_number(val: Any) -> Optional[float]:
    """様々な形式の数値をfloatに変換する"""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        if np.isnan(val) if isinstance(val, float) else False:
            return None
        return float(val)
    if isinstance(val, str):
        val = val.strip().replace(',', '').replace(' ', '').replace('\u3000', '')
        if val in ('', '-', '―', '－'):
            return 0.0
        # △ や ▲ はマイナス（日本の会計表記）
        if val.startswith(('△', '▲')):
            val = '-' + val[1:]
        # (1000) 形式のマイナス
        if val.startswith('(') and val.endswith(')'):
            val = '-' + val[1:-1]
        try:
            return float(val)
        except ValueError:
            return None
    return None


def _is_account_name(val: Any) -> bool:
    """値が会計科目名らしいかを判定する"""
    if not isinstance(val, str):
        return False
    val = val.strip()
    if len(val) < 2 or len(val) > 20:
        return False
    # 日本語文字を含む
    if not re.search(r'[\u3040-\u9fff]', val):
        return False
    # 数字だらけでない
    digit_ratio = sum(1 for c in val if c.isdigit()) / len(val)
    if digit_ratio > 0.5:
        return False
    return True


def parse_trial_balance(content: bytes) -> Tuple[Optional[Dict], Optional[str]]:
    """
    月次試算表ExcelをパースしてDict形式で返す
    戻り値: ({'accounts': {科目名: 残高}}, None) or (None, エラーメッセージ)
    """
    try:
        file_io = io.BytesIO(content)
        xl = pd.ExcelFile(file_io)

        best_data = None
        best_score = 0

        for sheet_name in xl.sheet_names:
            df = pd.read_excel(file_io, sheet_name=sheet_name,
                               header=None, dtype=object)
            data, score = _try_parse_trial(df)
            if data and score > best_score:
                best_data = data
                best_score = score

        if best_data is None or best_score < 3:
            return None, (
                '試算表の形式を認識できませんでした。\n'
                'Excelに会計科目名（仮払金、消耗品費など）が含まれているか確認してください。'
            )

        return best_data, None

    except Exception as e:
        return None, f'ファイルの読み込みに失敗しました: {e}'


def _normalize_name(val: Any) -> str:
    """科目名を正規化（スペース除去・全角半角統一）"""
    if not isinstance(val, str):
        return ''
    # 全角・半角スペースを除去し、前後トリム
    return val.replace('\u3000', '').replace(' ', '').strip()


def _try_parse_trial(df: pd.DataFrame) -> Tuple[Optional[Dict], int]:
    """試算表シートのパースを試みる"""
    if df.empty or len(df) < 5:
        return None, 0

    n_cols = len(df.columns)

    # 科目名列を探す（最も日本語科目名が多い列）
    account_col_idx = None
    best_jp_count = 0
    for col_idx in range(n_cols):
        col_vals = df.iloc[:, col_idx]
        jp_count = sum(
            1 for v in col_vals
            if _is_account_name(_normalize_name(v) if v is not None else '')
        )
        if jp_count > best_jp_count:
            best_jp_count = jp_count
            account_col_idx = col_idx

    if account_col_idx is None or best_jp_count < 3:
        return None, 0

    # 残高列を探す
    # ICS出力の列名（優先順）:
    #   当月残高      → 貸借対照表の試算表
    #   当月迄の累計  → 損益計算書の試算表
    #   当月累計      → 別表記
    # 注意: 「前月残高」「前月迄の累計」は前月値なので使わない
    BALANCE_HEADERS = ['当月残高', '当月迄の累計', '当月累計']
    balance_col_idx = None

    for target in BALANCE_HEADERS:
        for row_idx in range(min(10, len(df))):
            for col_idx in range(n_cols):
                if col_idx == account_col_idx:
                    continue
                cell = df.iloc[row_idx, col_idx]
                if isinstance(cell, str) and target in cell.replace(' ', '').replace('\u3000', ''):
                    balance_col_idx = col_idx
                    break
            if balance_col_idx is not None:
                break
        if balance_col_idx is not None:
            break

    # 見つからなければ「構成比率」「対売上比」などの%列を除いた最右端の数値列を使う
    if balance_col_idx is None:
        # まず%列(値が0〜100の小数)を除外
        pct_cols = set()
        for col_idx in range(n_cols):
            vals = [_clean_number(v) for v in df.iloc[:, col_idx] if v is not None]
            nums = [v for v in vals if v is not None]
            if nums and all(0 <= abs(v) <= 200 for v in nums) and any(v != int(v) for v in nums if v != 0):
                pct_cols.add(col_idx)

        for col_idx in range(n_cols - 1, -1, -1):
            if col_idx == account_col_idx or col_idx in pct_cols:
                continue
            col_vals = [_clean_number(v) for v in df.iloc[:, col_idx]]
            numeric_count = sum(1 for v in col_vals if v is not None)
            if numeric_count >= 5:
                balance_col_idx = col_idx
                break

    if balance_col_idx is None:
        return None, 0

    # データ抽出（科目名はスペース除去して正規化）
    accounts = {}
    for row_idx in range(len(df)):
        name_val = df.iloc[row_idx, account_col_idx]
        normalized = _normalize_name(name_val)
        if not _is_account_name(normalized):
            continue
        balance = _clean_number(df.iloc[row_idx, balance_col_idx])
        if balance is not None:
            # 同名科目は絶対値が大きい方（当月累計）を優先
            if normalized not in accounts or abs(balance) > abs(accounts[normalized]):
                accounts[normalized] = balance

    return ({'accounts': accounts}, len(accounts)) if accounts else (None, 0)


def parse_trend_table(content: bytes) -> Tuple[Optional[Dict], Optional[str]]:
    """
    比較損益推移表ExcelをパースしてDict形式で返す
    戻り値: ({'trend': {科目名: {月番号: 金額}}, 'months': [月番号リスト]}, None)
    """
    try:
        file_io = io.BytesIO(content)
        xl = pd.ExcelFile(file_io)

        best_data = None
        best_score = 0

        for sheet_name in xl.sheet_names:
            df = pd.read_excel(file_io, sheet_name=sheet_name,
                               header=None, dtype=object)
            data, score = _try_parse_trend(df)
            if data and score > best_score:
                best_data = data
                best_score = score

        if best_data is None or best_score < 6:
            return None, (
                '推移表の形式を認識できませんでした。\n'
                '月名（4月、5月…）と会計科目名が含まれているか確認してください。'
            )

        return best_data, None

    except Exception as e:
        return None, f'ファイルの読み込みに失敗しました: {e}'


def _extract_month_from_header(cell: Any) -> Optional[int]:
    """
    列ヘッダーから月番号を抽出する
    対応形式:
      「4月」「12月」          → 4, 12
      「6年8月」「7年1月」     → 8, 1  （ICS令和形式）
      「2024年4月」            → 4
    """
    if not isinstance(cell, str):
        return None
    cell = cell.strip()
    # 「N年M月」形式を優先（年付きは月だけ取り出す）
    m = re.search(r'\d+年(\d{1,2})月', cell)
    if m:
        return int(m.group(1))
    # 「M月」単体
    m = re.search(r'^(\d{1,2})月$', cell)
    if m:
        return int(m.group(1))
    # 数値のみ（1〜12）
    m = re.fullmatch(r'\d{1,2}', cell)
    if m:
        v = int(m.group())
        if 1 <= v <= 12:
            return v
    return None


def _try_parse_trend(df: pd.DataFrame) -> Tuple[Optional[Dict], int]:
    """推移表シートのパースを試みる"""
    if df.empty or len(df) < 5:
        return None, 0

    n_rows, n_cols = df.shape

    # 月名ヘッダー行を探す（月ヘッダーが3つ以上ある行）
    # ICS比較損益推移表: 「6年8月」「6年9月」…「7年7月」形式
    header_row_idx = None
    month_col_map: Dict[int, int] = {}  # col_idx -> 月番号
    # 年付き月ヘッダーを順序付きで保持するため、(col_idx, year, month) を収集
    month_col_ordered: list = []  # (col_idx, sort_key, month_num)

    for row_idx in range(min(20, n_rows)):
        found_ordered = []
        for col_idx in range(n_cols):
            cell = df.iloc[row_idx, col_idx]
            if not isinstance(cell, str):
                continue
            cell_s = cell.strip()
            # 年付き形式「6年8月」→ ソートキー=(year*12+month)
            m_yr = re.search(r'(\d+)年(\d{1,2})月', cell_s)
            if m_yr:
                yr, mo = int(m_yr.group(1)), int(m_yr.group(2))
                found_ordered.append((col_idx, yr * 12 + mo, mo))
                continue
            # 月のみ形式「4月」
            m_mo = re.search(r'^(\d{1,2})月$', cell_s)
            if m_mo:
                mo = int(m_mo.group(1))
                found_ordered.append((col_idx, mo, mo))

        if len(found_ordered) >= 3:
            header_row_idx = row_idx
            month_col_ordered = found_ordered
            break

    if header_row_idx is None or not month_col_ordered:
        return None, 0

    # ソートキーで並べ直し（会計年度順に整列）、重複月は最初のものを採用
    month_col_ordered.sort(key=lambda x: x[1])
    seen_months: set = set()
    for col_idx, sort_key, month_num in month_col_ordered:
        if month_num not in seen_months:
            seen_months.add(month_num)
            month_col_map[col_idx] = month_num

    if not month_col_map:
        return None, 0

    # 科目名列を探す（月列以外で日本語科目名が最も多い列）
    month_cols_set = set(month_col_map.keys())
    account_col_idx = None
    best_jp_count = 0
    for col_idx in range(n_cols):
        if col_idx in month_cols_set:
            continue
        col_vals = df.iloc[header_row_idx + 1:, col_idx]
        jp_count = sum(
            1 for v in col_vals
            if _is_account_name(_normalize_name(v) if v is not None else '')
        )
        if jp_count > best_jp_count:
            best_jp_count = jp_count
            account_col_idx = col_idx

    if account_col_idx is None or best_jp_count < 3:
        return None, 0

    # データ抽出（科目名スペース正規化）
    trend: Dict[str, Dict[int, float]] = {}
    # 月を会計年度順に並べるためのマッピング（col_idx -> 順序インデックス）
    ordered_months = [month_col_map[c] for c, _, _ in
                      sorted(month_col_ordered, key=lambda x: x[1])
                      if c in month_col_map]

    for row_idx in range(header_row_idx + 1, n_rows):
        name_val = df.iloc[row_idx, account_col_idx]
        normalized = _normalize_name(name_val)
        if not _is_account_name(normalized):
            continue
        monthly: Dict[int, float] = {}
        for col_idx, month_num in month_col_map.items():
            amount = _clean_number(df.iloc[row_idx, col_idx])
            if amount is not None:
                monthly[month_num] = amount
        if monthly:
            if normalized not in trend or len(monthly) > len(trend[normalized]):
                trend[normalized] = monthly

    # 月を会計年度順（期首から期末）に並べたリストを返す
    months = ordered_months if ordered_months else sorted(month_col_map.values())
    score = len(trend) * len(months)

    return ({'trend': trend, 'months': months}, score) if trend else (None, 0)
