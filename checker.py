# -*- coding: utf-8 -*-
"""
月次試算表・損益推移表のチェックロジック
マニュアルに基づく各種チェック項目を実装
"""
import numpy as np
from typing import Dict, List, Optional, Tuple


# チェック結果の重要度
LEVEL_ERROR = 'error'    # 要対応（赤）: 明らかに問題
LEVEL_WARNING = 'warning'  # 要確認（黄）: 内容確認が必要
LEVEL_INFO = 'info'      # 参考確認（青）: 余裕があれば確認


def run_all_checks(
    trial_data: Optional[Dict],
    trend_data: Optional[Dict]
) -> List[Dict]:
    """
    全チェックを実行し、結果リストを返す
    各結果: {level, category, account, message, action}
    """
    checks: List[Dict] = []

    # --- 試算表チェック ---
    if trial_data:
        accounts: Dict[str, float] = trial_data.get('accounts', {})
        checks.extend(_check_kari_accounts(accounts))
        checks.extend(_check_negative_balances(accounts))
        checks.extend(_check_zappi_trial(accounts))

    # --- 推移表チェック ---
    if trend_data:
        trend: Dict[str, Dict[int, float]] = trend_data.get('trend', {})
        months: List[int] = trend_data.get('months', [])
        checks.extend(_check_outliers(trend, months))
        checks.extend(_check_shomohin_threshold(trend))
        checks.extend(_check_shuzen_threshold(trend))
        checks.extend(_check_zappi_trend(trend))
        checks.extend(_check_sozei_trend(trend))
        checks.extend(_check_zasshu_trend(trend))
        checks.extend(_check_genka_depreciation(trend, months))
        checks.extend(_check_regular_movement(trend, months))

    # 重要度順にソート（error > warning > info）
    level_order = {LEVEL_ERROR: 0, LEVEL_WARNING: 1, LEVEL_INFO: 2}
    checks.sort(key=lambda x: level_order.get(x['level'], 3))

    return checks


# ─────────────────────────────────────────────────
# 試算表チェック
# ─────────────────────────────────────────────────

def _check_kari_accounts(accounts: Dict[str, float]) -> List[Dict]:
    """STEP2: 仮払金・仮受金の残高チェック"""
    results = []
    for name, balance in accounts.items():
        for keyword, hint in [('仮払金', '何に使われたか'), ('仮受金', '何で入ってきたか')]:
            if keyword not in name:
                continue
            if balance != 0:
                results.append({
                    'level': LEVEL_ERROR,
                    'category': '仮勘定の残高あり',
                    'account': name,
                    'message': f'【{name}】に残高があります（{balance:,.0f}円）',
                    'action': (
                        f'総勘定元帳の摘要欄や領収書で「{hint}」を確認し、'
                        '正しい科目（消耗品費・会議費・売上など）に振替仕訳を入力してください。'
                        '不明な場合はお客様にご確認ください。'
                    ),
                })
    return results


def _check_negative_balances(accounts: Dict[str, float]) -> List[Dict]:
    """STEP3: 本来プラスであるはずの科目のマイナス残高チェック"""
    # 通常プラス残高であるべき科目キーワード
    SHOULD_BE_POSITIVE = [
        '現金', '当座預金', '普通預金', '定期預金',
        '売掛金', '受取手形', '未収金', '前払費用', '前払金', '立替金',
        '商品', '製品', '原材料', '仕掛品',
        '建物', '構築物', '機械装置', '車両運搬具', '器具備品', '工具', '土地',
        '敷金', '保証金',
        '仕入', '外注費', '給料手当', '給与', '役員報酬',
        '福利厚生費', '旅費交通費', '通信費', '水道光熱費',
        '消耗品費', '修繕費', '地代家賃', '賃借料',
        '保険料', '広告宣伝費', '接待交際費', '会議費',
    ]
    results = []
    for name, balance in accounts.items():
        for keyword in SHOULD_BE_POSITIVE:
            if keyword in name and balance < -100:  # 端数誤差を考慮して -100 以下
                results.append({
                    'level': LEVEL_ERROR,
                    'category': 'マイナス残高（入力誤りの可能性）',
                    'account': name,
                    'message': f'【{name}】がマイナス残高です（{balance:,.0f}円）',
                    'action': (
                        '総勘定元帳を確認し、入力漏れ・二重計上・金額の打ち間違いがないか'
                        'チェックしてください。'
                    ),
                })
                break
    return results


def _check_zappi_trial(accounts: Dict[str, float]) -> List[Dict]:
    """試算表の雑費チェック"""
    results = []
    for name, balance in accounts.items():
        if '雑費' in name and balance != 0:
            results.append({
                'level': LEVEL_WARNING,
                'category': '雑費の使用',
                'account': name,
                'message': f'【{name}】に残高があります（{balance:,.0f}円）',
                'action': (
                    '雑費は原則使用しません。内容を確認し、'
                    '会議費・消耗品費・交通費など適切な科目に変更してください。'
                ),
            })
    return results


# ─────────────────────────────────────────────────
# 推移表チェック
# ─────────────────────────────────────────────────

def _find_account_in_trend(
    trend: Dict[str, Dict[int, float]],
    keyword: str
) -> List[Tuple[str, Dict[int, float]]]:
    """科目名にキーワードを含む科目を全て返す"""
    return [(name, data) for name, data in trend.items() if keyword in name]


def _check_outliers(
    trend: Dict[str, Dict[int, float]],
    months: List[int]
) -> List[Dict]:
    """STEP3: 主要科目の月次異常値チェック（急増・急減・ゼロ）"""
    TARGET_KEYWORDS = ['売上高', '売上', '仕入高', '仕入', '外注費', '給料手当', '給与', '役員報酬']
    # 完全一致優先：部分一致で重複しないよう科目を一度だけ処理
    processed = set()
    results = []

    for kw in TARGET_KEYWORDS:
        for name, monthly_data in trend.items():
            if name in processed:
                continue
            if kw not in name:
                continue
            processed.add(name)

            if len(monthly_data) < 2:
                continue

            sorted_months = sorted(monthly_data.keys())
            amounts = [monthly_data[m] for m in sorted_months]
            non_zero = [a for a in amounts if a > 0]

            if len(non_zero) < 2:
                continue

            median_val = float(np.median(non_zero))
            if median_val < 10000:  # 金額が小さい科目はスキップ
                continue

            for month, amount in zip(sorted_months, amounts):
                # ゼロ（計上漏れ）の疑い
                if amount == 0 and median_val >= 50000:
                    results.append({
                        'level': LEVEL_WARNING,
                        'category': '計上漏れの疑い',
                        'account': name,
                        'message': (
                            f'【{name}】{month}月が0円です'
                            f'（他月中央値: {median_val:,.0f}円）'
                        ),
                        'action': (
                            f'総勘定元帳で{month}月を確認し、'
                            '計上漏れがないかチェックしてください。'
                        ),
                    })
                # 突出して高い（二重計上の疑い）
                elif amount > median_val * 2.5 and amount > median_val + 100000:
                    results.append({
                        'level': LEVEL_WARNING,
                        'category': '異常値（突出して高い）',
                        'account': name,
                        'message': (
                            f'【{name}】{month}月が突出して高い'
                            f'（{amount:,.0f}円、他月中央値: {median_val:,.0f}円）'
                        ),
                        'action': (
                            f'総勘定元帳で{month}月を確認し、'
                            '二重計上がないかチェックしてください。'
                        ),
                    })
                # 極端に低い（ゼロ除く）
                elif 0 < amount < median_val * 0.3 and median_val - amount > 100000:
                    results.append({
                        'level': LEVEL_WARNING,
                        'category': '異常値（突出して低い）',
                        'account': name,
                        'message': (
                            f'【{name}】{month}月が極端に少ない'
                            f'（{amount:,.0f}円、他月中央値: {median_val:,.0f}円）'
                        ),
                        'action': (
                            f'総勘定元帳で{month}月を確認し、'
                            '計上漏れがないかチェックしてください。'
                        ),
                    })
    return results


def _check_shomohin_threshold(trend: Dict[str, Dict[int, float]]) -> List[Dict]:
    """消耗品費の10万円以上月チェック（固定資産の可能性）"""
    THRESHOLD = 100_000
    results = []
    for name, monthly_data in trend.items():
        if '消耗品費' not in name:
            continue
        for month, amount in monthly_data.items():
            if amount >= THRESHOLD:
                results.append({
                    'level': LEVEL_WARNING,
                    'category': '固定資産の可能性（消耗品費）',
                    'account': name,
                    'message': (
                        f'【{name}】{month}月が{amount:,.0f}円'
                        f'（10万円以上）'
                    ),
                    'action': (
                        f'総勘定元帳で{month}月の明細を確認し、'
                        '1取引10万円以上のものがないか確認してください。'
                        '該当する場合は固定資産登録（器具備品等）が必要です。'
                        '（例：パソコン、応接セットなど）'
                    ),
                })
    return results


def _check_shuzen_threshold(trend: Dict[str, Dict[int, float]]) -> List[Dict]:
    """修繕費の20万円以上月チェック（資産計上の可能性）"""
    THRESHOLD = 200_000
    results = []
    for name, monthly_data in trend.items():
        if '修繕費' not in name:
            continue
        for month, amount in monthly_data.items():
            if amount >= THRESHOLD:
                results.append({
                    'level': LEVEL_WARNING,
                    'category': '資本的支出の可能性（修繕費）',
                    'account': name,
                    'message': (
                        f'【{name}】{month}月が{amount:,.0f}円'
                        f'（20万円以上）'
                    ),
                    'action': (
                        f'工事の契約書・請求書で内容を確認してください。'
                        '資産の価値を高める修繕や耐用年数を延ばす修繕は'
                        '資産計上（資本的支出）が必要です。'
                        '判断に迷ったら先輩や租税調査研究会に相談してください。'
                    ),
                })
    return results


def _check_zappi_trend(trend: Dict[str, Dict[int, float]]) -> List[Dict]:
    """推移表の雑費チェック"""
    results = []
    for name, monthly_data in trend.items():
        if '雑費' not in name:
            continue
        flagged = [(m, a) for m, a in monthly_data.items() if a != 0]
        if flagged:
            months_str = '・'.join(
                f'{m}月（{a:,.0f}円）' for m, a in sorted(flagged)
            )
            results.append({
                'level': LEVEL_WARNING,
                'category': '雑費の使用',
                'account': name,
                'message': f'【{name}】に残高があります: {months_str}',
                'action': (
                    '雑費は原則使用しません。内容を確認し、'
                    '会議費・消耗品費など適切な科目に変更してください。'
                ),
            })
    return results


def _check_sozei_trend(trend: Dict[str, Dict[int, float]]) -> List[Dict]:
    """租税公課の内容確認チェック"""
    results = []
    for name, monthly_data in trend.items():
        if '租税公課' not in name:
            continue
        active_months = [m for m, a in monthly_data.items() if a != 0]
        if active_months:
            results.append({
                'level': LEVEL_INFO,
                'category': '租税公課の内容確認',
                'account': name,
                'message': f'【{name}】に残高があります（{len(active_months)}か月）',
                'action': (
                    '経費にならない税金（法人税・所得税・延滞税など）が含まれていないか確認してください。'
                    '摘要欄に「印紙代」「固定資産税」「自動車税」など内容が記載されているか確認し、'
                    '記載がなければ入力担当者に追記を依頼してください。'
                ),
            })
    return results


def _check_zasshu_trend(trend: Dict[str, Dict[int, float]]) -> List[Dict]:
    """雑収入の消費税区分確認チェック"""
    results = []
    for name, monthly_data in trend.items():
        if '雑収入' not in name:
            continue
        active_months = [m for m, a in monthly_data.items() if a != 0]
        if active_months:
            results.append({
                'level': LEVEL_INFO,
                'category': '雑収入の消費税区分確認',
                'account': name,
                'message': f'【{name}】に残高があります（{len(active_months)}か月）',
                'action': (
                    '消費税区分に間違いがないか確認してください。\n'
                    '・補助金・助成金 → 「対象外」\n'
                    '・保険の解約返戻金 → 「対象外」\n'
                    '・作業くずの売却代金 → 「課税」\n'
                    '内容が分かるよう摘要欄にも記載があるか確認してください。'
                ),
            })
    return results


def _check_genka_depreciation(
    trend: Dict[str, Dict[int, float]],
    months: List[int]
) -> List[Dict]:
    """減価償却費の計上確認チェック"""
    results = []
    for name, monthly_data in trend.items():
        if '減価償却費' not in name:
            continue

        zero_months = sorted(m for m in months if monthly_data.get(m, 0) == 0)
        nonzero_months = sorted(m for m in months if monthly_data.get(m, 0) != 0)

        if nonzero_months and zero_months:
            # 一部の月だけ計上されていない
            months_str = '・'.join(f'{m}月' for m in zero_months)
            results.append({
                'level': LEVEL_WARNING,
                'category': '減価償却費の計上漏れの可能性',
                'account': name,
                'message': (
                    f'【{name}】が計上されていない月があります: {months_str}'
                ),
                'action': (
                    '毎月の減価償却費が正しく計上されているか確認してください。'
                    '期中に固定資産の増減がある場合、ICSの設定変更漏れにご注意ください。'
                ),
            })
        elif not nonzero_months and months:
            # 全月ゼロ（固定資産がある場合は要確認）
            results.append({
                'level': LEVEL_INFO,
                'category': '減価償却費の計上確認',
                'account': name,
                'message': f'【{name}】が全月0円です',
                'action': (
                    '固定資産がある場合、減価償却費が正しく計上されているか確認してください。'
                    '顧問料年額60万円以上の先はICSの毎月自動計上設定を確認してください。'
                ),
            })
    return results


def _check_regular_movement(
    trend: Dict[str, Dict[int, float]],
    months: List[int]
) -> List[Dict]:
    """
    定期的に動くはずの科目が止まっていないかチェック
    （長期借入金返済・保険料・リース料など）
    """
    if not months or len(months) < 3:
        return []

    # 推移表が損益計算書のみの場合、貸借科目は含まれないことが多い
    # ここでは損益科目で毎月計上されるはずのものをチェック
    REGULAR_KEYWORDS = ['保険料', 'リース料', '賃借料']

    results = []
    for name, monthly_data in trend.items():
        for kw in REGULAR_KEYWORDS:
            if kw not in name:
                continue

            # 半分以上の月にデータがある科目で、途中でゼロになっている月を探す
            nonzero = [m for m in months if monthly_data.get(m, 0) != 0]
            zero = [m for m in months if monthly_data.get(m, 0) == 0]

            if len(nonzero) >= len(months) * 0.5 and zero:
                months_str = '・'.join(f'{m}月' for m in sorted(zero))
                results.append({
                    'level': LEVEL_INFO,
                    'category': '定期的支払の停止確認',
                    'account': name,
                    'message': (
                        f'【{name}】に計上がない月があります: {months_str}'
                    ),
                    'action': (
                        '資料の未着・入力漏れ、または科目誤入力の可能性があります。'
                        'お客様からの資料を確認してください。'
                    ),
                })
            break  # 同じ科目で複数キーワードが一致しても1回だけ

    return results
