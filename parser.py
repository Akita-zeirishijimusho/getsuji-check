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
        jp_count = sum(1 for v in col_vals if _is_account_name(str(v) if v is not None else ''))
        if jp_count > best_jp_count:
            best_jp_count = jp_count
            account_col_idx = col_idx

    if account_col_idx is None or best_jp_count < 3:
        return None, 0

    # 残高列を探す
    # 「残高」という文字列があればその列を優先
    balance_col_idx = None
    for row_idx in range(min(10, len(df))):
        for col_idx in range(n_cols):
            cell = df.iloc[row_idx, col_idx]
            if isinstance(cell, str) and '残高' in cell and col_idx != account_col_idx:
                balance_col_idx = col_idx
                break
        if balance_col_idx is not None:
            break

    # 見つからなければ最右端の数値列を使う
    if balance_col_idx is None:
        for col_idx in range(n_cols - 1, -1, -1):
            if col_idx == account_col_idx:
                continue
            col_vals = [_clean_number(v) for v in df.iloc[:, col_idx]]
            numeric_count = sum(1 for v in col_vals if v is not None)
            if numeric_count >= 5:
                balance_col_idx = col_idx
                break

    if balance_col_idx is None:
        return None, 0

    # データ抽出
    accounts = {}
    for row_idx in range(len(df)):
        name_val = df.iloc[row_idx, account_col_idx]
        if not _is_account_name(str(name_val) if name_val is not None else ''):
            continue
        name = str(name_val).strip()
        balance = _clean_number(df.iloc[row_idx, balance_col_idx])
        if balance is not None:
            accounts[name] = balance

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


def _try_parse_trend(df: pd.DataFrame) -> Tuple[Optional[Dict], int]:
    """推移表シートのパースを試みる"""
    if df.empty or len(df) < 5:
        return None, 0

    n_rows, n_cols = df.shape

    # 月名ヘッダー行を探す（「4月」「5月」などが3つ以上ある行）
    header_row_idx = None
    month_col_map: Dict[int, int] = {}  # col_idx -> 月番号

    for row_idx in range(min(20, n_rows)):
        found = {}
        for col_idx in range(n_cols):
            cell = df.iloc[row_idx, col_idx]
            if isinstance(cell, str):
                m = MONTH_PATTERN.search(cell)
                if m:
                    found[col_idx] = int(m.group(1))
            elif isinstance(cell, (int, float)) and not (isinstance(cell, float) and np.isnan(cell)):
                # 数値そのものが月を示す場合（例: 4, 5, 6...）
                if 1 <= int(cell) <= 12:
                    # 同じ行の他のセルを見て月の連番らしければ採用
                    pass
        if len(found) >= 3:
            header_row_idx = row_idx
            month_col_map = found
            break

    if header_row_idx is None or not month_col_map:
        return None, 0

    # 「合計」「前年」など月以外の列を除外（月番号が重複している列も除外）
    seen_months = set()
    filtered_month_col_map = {}
    for col_idx in sorted(month_col_map.keys()):
        m = month_col_map[col_idx]
        if m not in seen_months:
            seen_months.add(m)
            filtered_month_col_map[col_idx] = m
    month_col_map = filtered_month_col_map

    # 科目名列を探す（月列より左にある日本語列）
    month_cols_set = set(month_col_map.keys())
    account_col_idx = None
    best_jp_count = 0
    for col_idx in range(n_cols):
        if col_idx in month_cols_set:
            continue
        col_vals = df.iloc[header_row_idx + 1:, col_idx]
        jp_count = sum(1 for v in col_vals if _is_account_name(str(v) if v is not None else ''))
        if jp_count > best_jp_count:
            best_jp_count = jp_count
            account_col_idx = col_idx

    if account_col_idx is None or best_jp_count < 3:
        return None, 0

    # データ抽出
    trend: Dict[str, Dict[int, float]] = {}
    for row_idx in range(header_row_idx + 1, n_rows):
        name_val = df.iloc[row_idx, account_col_idx]
        if not _is_account_name(str(name_val) if name_val is not None else ''):
            continue
        name = str(name_val).strip()
        monthly: Dict[int, float] = {}
        for col_idx, month_num in month_col_map.items():
            amount = _clean_number(df.iloc[row_idx, col_idx])
            if amount is not None:
                monthly[month_num] = amount
        if monthly:
            # 同名科目は後のものを優先（合計行などを避けるため、データ行数が多い方を採用）
            if name not in trend or len(monthly) > len(trend[name]):
                trend[name] = monthly

    months = sorted(month_col_map.values())
    score = len(trend) * len(months)

    return ({'trend': trend, 'months': months}, score) if trend else (None, 0)
