# -*- coding: utf-8 -*-
"""
月次試算表チェックツール - Flask アプリケーション
秋田税理士事務所 内部ツール
"""
import traceback
from flask import Flask, render_template, request, jsonify

from parser import parse_trial_balance, parse_trend_table
from checker import run_all_checks

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 32 * 1024 * 1024  # 32MB 上限
app.config['JSON_AS_ASCII'] = False  # 日本語JSONを文字化けさせない


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/check', methods=['POST'])
def check():
    """
    Excelファイルを受け取り、チェック結果をJSONで返す
    フォームフィールド:
      trial - 月次試算表 (.xlsx/.xls)
      trend - 比較損益推移表 (.xlsx/.xls)
    """
    try:
        errors = []
        trial_data = None
        trend_data = None

        # 試算表の読み込み
        trial_file = request.files.get('trial')
        if trial_file and trial_file.filename:
            content = trial_file.read()
            trial_data, err = parse_trial_balance(content)
            if err:
                errors.append(f'試算表の読み込みエラー: {err}')

        # 推移表の読み込み
        trend_file = request.files.get('trend')
        if trend_file and trend_file.filename:
            content = trend_file.read()
            trend_data, err = parse_trend_table(content)
            if err:
                errors.append(f'推移表の読み込みエラー: {err}')

        if trial_data is None and trend_data is None:
            return jsonify({
                'checks': [],
                'summary': {'error': 0, 'warning': 0, 'info': 0},
                'errors': errors or ['ファイルがアップロードされていません。'],
                'parsed': {},
            })

        # チェック実行
        checks = run_all_checks(trial_data, trend_data)

        # サマリー集計
        summary = {
            'error': sum(1 for c in checks if c['level'] == 'error'),
            'warning': sum(1 for c in checks if c['level'] == 'warning'),
            'info': sum(1 for c in checks if c['level'] == 'info'),
        }

        # パース結果の概要（確認用）
        parsed = {}
        if trial_data:
            accounts = trial_data.get('accounts', {})
            parsed['trial'] = {
                'count': len(accounts),
                'accounts': list(accounts.keys())[:20],  # 最大20科目を表示
            }
        if trend_data:
            trend = trend_data.get('trend', {})
            months = trend_data.get('months', [])
            parsed['trend'] = {
                'months': months,
                'count': len(trend),
                'accounts': list(trend.keys())[:20],
            }

        return jsonify({
            'checks': checks,
            'summary': summary,
            'errors': errors,
            'parsed': parsed,
        })

    except Exception as e:
        return jsonify({
            'checks': [],
            'summary': {'error': 0, 'warning': 0, 'info': 0},
            'errors': [f'予期しないエラーが発生しました: {e}'],
            'parsed': {},
            'trace': traceback.format_exc(),
        }), 500


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
