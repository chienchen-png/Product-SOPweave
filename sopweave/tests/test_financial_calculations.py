"""Financial calculations and preprocessing use synthetic data only."""
import unittest

import pandas as pd

from services import finmod_refs
from tools._finmod_models import MODEL_REGISTRY, run_model
from tools.finmod_eval import _finmod_eval, materialize_derived
from tools.finmod_prep import _apply_cast, _apply_clean, _apply_pivot


class FinancialCalculationTests(unittest.TestCase):
    def test_registered_models_match_hand_calculated_results(self):
        df_simple = pd.DataFrame(
            {'收入': [100, 200, 300, 400],
             '成本': [60, 120, 180, 240],
             '变动成本': [60, 120, 180, 240],
             '净利润': [30, 60, 90, 120],
             '期初留存收益': [100, 130, 190, 280],
             '股利': [0, 0, 0, 0],
             '经营活动净流量': [50, 80, 100, 120],
             '投资活动净流量': [-20, -30, -10, -25],
             '筹资活动净流量': [0, 0, 0, 0],
             '期初现金': [200, 230, 280, 370],
             '资产': [1000, 1000, 1000, 1000],
             '负债': [400, 400, 400, 400],
             '所有者权益': [600, 600, 600, 600],
             '营业收入': [100, 200, 300, 400],
             '营业成本': [60, 120, 180, 240],
             '费用': [10, 20, 30, 40],
             '税金': [0, 0, 0, 0],
             '销售额': [100, 120, 110, 130],
             '实际成本': [100, 200, 300, 400],
             '标准成本': [90, 190, 280, 380],
             '实际值': [100, 200, 300, 400],
             '预算值': [110, 190, 310, 390],
             '单价': [10, 10, 10, 10],
             '单位变动成本': [6, 6, 6, 6],
             '固定成本': [100, 100, 100, 100],
             '销量': [50, 60, 55, 70],
             '应收账款': [80, 90, 85, 100],
             '存货': [40, 45, 42, 50],
             '应付账款': [30, 35, 32, 40],
             '账龄天数': [10, 40, 70, 200],
             '权益市值': [6000000, 6000000, 6000000, 6000000],
             '债务市值': [4000000, 4000000, 4000000, 4000000]}
        )
        df_simple['利润'] = df_simple['收入'] - df_simple['成本']
        self.assertEqual(len(MODEL_REGISTRY), 32)
        r = run_model('a1-three-statement', df_simple, {})
        with self.subTest(case='A1 三表联动：期末留存=400'):
            self.assertTrue(r['ok'] and r['summary']['期末留存收益'] == 400, str(r.get('summary')))
        with self.subTest(case='A1 资产=负债+权益'):
            self.assertTrue(r['summary']['资产=负债+权益'] is True, '')
        r = run_model('a2-income-forecast', df_simple, {'growth_rate': 0.05})
        with self.subTest(case='A2 利润表预测：营收增长5%→420'):
            self.assertTrue(r['ok'] and r['summary']['预测营业收入'] == 420, str(r.get('summary')))
        r = run_model('a3-cashflow-forecast', df_simple, {'horizon': 2})
        with self.subTest(case='A3 现金流预测：2 期线性外推'):
            self.assertTrue(r['ok'] and len(r['summary']['预测值']) == 2, str(r.get('summary')))
        df_units = pd.DataFrame({'单元': ['A', 'A', 'B'], '收入': [100, 200, 300], '成本': [60, 100, 150]})
        r = run_model('a4-consolidation', df_units, {'dim_col': '单元'})
        with self.subTest(case='A4 合并汇总：2 单元'):
            self.assertTrue(r['ok'] and r['summary']['单元数'] == 2, str(r.get('summary')))
        r = run_model('b1-budget', df_simple, {'growth_rate': 0.1})
        with self.subTest(case='B1 年度预算：收入预算 440'):
            self.assertTrue(r['ok'] and r['summary']['收入']['预算值'] == 440, str(r.get('summary')))
        r = run_model('b2-rolling-forecast', df_simple, {'window': 3, 'horizon': 2})
        with self.subTest(case='B2 滚动预测：2 期'):
            self.assertTrue(r['ok'] and len(r['summary']['预测值']) == 2, str(r.get('summary')))
        r = run_model('b3-sales-forecast', df_simple, {'method': 'moving_avg', 'horizon': 2})
        with self.subTest(case='B3 销售预测：移动平均'):
            self.assertTrue(r['ok'] and len(r['summary']['预测值']) == 2, str(r.get('summary')))
        r = run_model('b4-cost-budget', df_simple, {'growth_rate': 0.05})
        with self.subTest(case='B4 成本预算：营业成本占收入60%'):
            self.assertTrue(r['ok'] and r['summary']['营业成本']['占收入比'] == 0.6, str(r.get('summary')))
        r = run_model('c1-cvp-breakeven', df_simple, {})
        with self.subTest(case='C1 本量利：盈亏平衡销量 25'):
            self.assertTrue(r['ok'] and r['summary']['盈亏平衡销量'] == 25.0, str(r.get('summary')))
        r = run_model('c2-contribution-margin', df_simple, {})
        with self.subTest(case='C2 边际贡献：总贡献 400'):
            self.assertTrue(r['ok'] and r['summary']['总边际贡献'] == 400, str(r.get('summary')))
        r = run_model('c3-cost-variance', df_simple, {})
        with self.subTest(case='C3 成本差异：总差异 60'):
            self.assertTrue(r['ok'] and r['summary']['总差异'] == 60, str(r.get('summary')))
        r = run_model('c4-product-profitability', df_units, {'dim_col': '单元'})
        with self.subTest(case='C4 产品线盈利：2 单元'):
            self.assertTrue(r['ok'] and len(r['summary']['盈利Top']) == 2, str(r.get('summary')))
        df_dupont = pd.DataFrame({'净利润': [100], '营业收入': [1000], '总资产': [2000], '所有者权益': [800]})
        r = run_model('d1-dupont', df_dupont, {})
        with self.subTest(case='D1 杜邦：ROE=0.125'):
            self.assertTrue(r['ok'] and r['summary']['ROE'] == 0.125, str(r.get('summary')))
        r = run_model('d2-financial-ratios', df_dupont, {})
        with self.subTest(case='D2 五维比率：净利率 0.1'):
            self.assertTrue(r['ok'] and r['summary']['净利率'] == 0.1, str(r.get('summary')))
        r = run_model('d3-budget-variance', df_simple, {})
        with self.subTest(case='D3 预算差异：总差异 0'):
            self.assertTrue(r['ok'] and r['summary']['总差异'] == 0, str(r.get('summary')))
        r = run_model('d4-yoy-mom', df_simple, {'period_col': '收入'})
        with self.subTest(case='D4 同比环比：可执行'):
            self.assertTrue(r['ok'], str(r.get('summary')))
        r = run_model('d5-leverage', df_simple, {})
        with self.subTest(case='D5 杠杆：DOL=1.5556'):
            self.assertTrue(r['ok'] and abs(r['summary']['经营杠杆DOL'] - 1.5556) < 0.01, str(r.get('summary')))
        df_cf = pd.DataFrame({'现金流': [-1000, 300, 400, 500]})
        r = run_model('e1-npv-irr', df_cf, {'discount_rate': 0.1})
        with self.subTest(case='E1 NPV≈-21.04'):
            self.assertTrue(r['ok'] and abs(r['summary']['NPV'] - -21.04) < 0.1, str(r.get('summary')))
        r = run_model('e2-wacc', df_simple, {'cost_of_equity': 0.12, 'cost_of_debt': 0.05, 'tax_rate': 0.25})
        with self.subTest(case='E2 WACC=8.7%'):
            self.assertTrue(r['ok'] and abs(r['summary']['WACC'] - 8.7) < 0.01, str(r.get('summary')))
        r = run_model('e3-sensitivity', df_simple, {'formula': '收入-成本'})
        with self.subTest(case='E3 敏感性：基准 160'):
            self.assertTrue(r['ok'] and abs(r['summary']['基准结果'] - 160) < 0.01, str(r.get('summary')))
        r = run_model('e4-scenario', df_simple, {'formula': '收入-成本', 'scenarios': {'乐观': {'收入': 1.1}, '悲观': {'收入': 0.9}}})
        with self.subTest(case='E4 情景：乐观 200'):
            self.assertTrue(r['ok'] and r['summary']['情景结果']['乐观'] == 200, str(r.get('summary')))
        r = run_model('e5-project-breakeven', df_simple, {})
        with self.subTest(case='E5 项目盈亏平衡销量 25'):
            self.assertTrue(r['ok'] and r['summary']['盈亏平衡销量'] == 25.0, str(r.get('summary')))
        r = run_model('e6-equipment-replacement', df_simple, {'cost': 100000, 'salvage': 10000, 'life': 10, 'discount_rate': 0.1})
        with self.subTest(case='E6 设备更新 EAC 可算'):
            self.assertTrue(r['ok'] and r['summary']['年均成本EAC'] > 0, str(r.get('summary')))
        r = run_model('f1-cash-cycle', df_simple, {})
        with self.subTest(case='F1 现金周期 CCC≈106.46'):
            self.assertTrue(r['ok'] and abs(r['summary']['现金周期CCC'] - 106.46) < 0.1, str(r.get('summary')))
        r = run_model('f2-aging-analysis', df_simple, {})
        with self.subTest(case='F2 账龄：4 段分布'):
            self.assertTrue(r['ok'] and len(r['summary']['账龄分布']) == 4, str(r.get('summary')))
        r = run_model('f3-inventory-turnover', df_simple, {})
        with self.subTest(case='F3 存货周转率 4.8'):
            self.assertTrue(r['ok'] and r['summary']['存货周转率'] == 4.8, str(r.get('summary')))
        r = run_model('f4-wcr', df_simple, {})
        with self.subTest(case='F4 WCR=110'):
            self.assertTrue(r['ok'] and r['summary']['营运资金需求WCR'] == 110, str(r.get('summary')))
        df_stats = pd.DataFrame({'x_i': [1, 2, 3, None]})
        r = run_model('g1-descriptive-stats', df_stats, {})
        with self.subTest(case='G1 描述统计可执行'):
            self.assertTrue(r['ok'] and 'x_i' in r['summary'], str(r.get('summary')))
        with self.subTest(case='G1 x_i 均值 2'):
            self.assertTrue(r['summary']['x_i']['均值'] == 2, '')
        r = run_model('g2-correlation', df_simple, {})
        with self.subTest(case='G2 相关矩阵：列数 ≥ 2'):
            self.assertTrue(r['ok'] and len(r['summary']['matrix']) >= 2, str(r.get('summary')))
        df_regression = pd.DataFrame({'x_i': [1, 2, 3, 4], 'y': [3, 5, 7, 9]})
        r = run_model('g3-regression', df_regression, {})
        with self.subTest(case='G3 回归：R² 可算'):
            self.assertTrue(r['ok'] and r['summary']['R²'] is not None, str(r.get('summary')))
        r = run_model('g4-time-series-decompose', df_simple, {})
        with self.subTest(case='G4 时序分解：趋势存在'):
            self.assertTrue(r['ok'] and r['summary']['末值'] is not None, str(r.get('summary')))
        r = run_model('g5-forecasting', df_simple, {'horizon': 2, 'method': 'linear'})
        with self.subTest(case='G5 预测：2 期'):
            self.assertTrue(r['ok'] and len(r['summary']['预测值']) == 2, str(r.get('summary')))

    def test_statistics_and_derived_formula_broadcast(self):
        g1 = finmod_refs.get_model_detail('g1-descriptive-stats')
        varmap = {v['var_name']: v for v in (g1 or {}).get('variables', [])}
        with self.subTest(case='G1 x_i → input'):
            self.assertTrue(varmap.get('x_i', {}).get('direction') == 'input', str(varmap.get('x_i')))
        with self.subTest(case='G1 n → statistic'):
            self.assertTrue(varmap.get('n', {}).get('direction') == 'statistic', str(varmap.get('n')))
        with self.subTest(case='G1 x̄ → statistic'):
            self.assertTrue(varmap.get('x̄', {}).get('direction') == 'statistic', str(varmap.get('x̄')))
        with self.subTest(case='G1 s → statistic'):
            self.assertTrue(varmap.get('s', {}).get('direction') == 'statistic', str(varmap.get('s')))
        g3 = finmod_refs.get_model_detail('g3-regression')
        v3 = {v['var_name']: v for v in (g3 or {}).get('variables', [])}
        with self.subTest(case='G3 y → input'):
            self.assertTrue(v3.get('y', {}).get('direction') == 'input', str(v3.get('y')))
        with self.subTest(case='G3 b_i → statistic'):
            self.assertTrue(v3.get('b_i', {}).get('direction') == 'statistic', str(v3.get('b_i')))
        with self.subTest(case='G3 R² → statistic'):
            self.assertTrue(v3.get('R²', {}).get('direction') == 'statistic', str(v3.get('R²')))
        df = pd.DataFrame({'回款总额': [100.0, 200.0, 300.0, 400.0]})
        ns = {'回款总额': df['回款总额']}
        with self.subTest(case='MEAN=250'):
            self.assertTrue(abs(_finmod_eval('MEAN(回款总额)', ns) - 250.0) < 1e-09, '')
        with self.subTest(case='STD≈129.1'):
            self.assertTrue(abs(_finmod_eval('STD(回款总额)', ns) - 129.099) < 0.01, '')
        with self.subTest(case='MEDIAN=250'):
            self.assertTrue(abs(_finmod_eval('MEDIAN(回款总额)', ns) - 250.0) < 1e-09, '')
        with self.subTest(case='VAR≈16666.7'):
            self.assertTrue(abs(_finmod_eval('VAR(回款总额)', ns) - 16666.67) < 0.1, '')
        with self.subTest(case='COUNT=4'):
            self.assertTrue(_finmod_eval('COUNT(回款总额)', ns) == 4, '')
        out_df, results, errors = materialize_derived(df, ['x̄=MEAN(回款总额)', 'n=COUNT(回款总额)', 's=STD(回款总额)'])
        with self.subTest(case='无错误'):
            self.assertTrue(len(errors) == 0, str(errors))
        with self.subTest(case='x̄ 广播整列 4 行全 250'):
            self.assertTrue(list(out_df['x̄']) == [250.0, 250.0, 250.0, 250.0], str(list(out_df['x̄'])))
        with self.subTest(case='n 广播整列 4'):
            self.assertTrue(list(out_df['n']) == [4, 4, 4, 4], str(list(out_df['n'])))
        with self.subTest(case='x̄ broadcast=True'):
            self.assertTrue(results['x̄']['broadcast'] is True, str(results['x̄']))
        with self.subTest(case='s≈129.1'):
            self.assertTrue(abs(float(out_df['s'][0]) - 129.099) < 0.01, '')
        out2, res2, err2 = materialize_derived(df, ['x̄=MEAN(回款总额)', '偏离=x̄-回款总额'])
        with self.subTest(case='无错误'):
            self.assertTrue(len(err2) == 0, str(err2))
        with self.subTest(case='偏离=均值-各行'):
            self.assertTrue(list(out2['偏离']) == [150.0, 50.0, -50.0, -150.0], str(list(out2['偏离'])))
        out3, res3, err3 = materialize_derived(df, ['lag1=LAG(回款总额,1)', 'chg=PCT_CHANGE(回款总额,1)'])
        with self.subTest(case='无错误'):
            self.assertTrue(len(err3) == 0, str(err3))
        with self.subTest(case='LAG 前移'):
            self.assertTrue(pd.isna(out3['lag1'][0]) and list(out3['lag1'][1:]) == [100.0, 200.0, 300.0], str(list(out3['lag1'])))
        with self.subTest(case='PCT_CHANGE'):
            self.assertTrue(abs(float(out3['chg'][1]) - 1.0) < 1e-09 and abs(float(out3['chg'][2]) - 0.5) < 1e-09, str(list(out3['chg'])))

    def test_clean_cast_and_pivot_preserve_expected_values(self):
        df = pd.DataFrame({'业务员': ['A', 'A', 'B'], '金额': [1, 2, 3]})
        out, log = _apply_clean(df, {'op': 'clean', 'action': 'dedup'})
        with self.subTest(case='去重 3→3（无重复行）'):
            self.assertTrue(len(out) == 3, f'len={len(out)}')
        df2 = pd.DataFrame({'业务员': ['A', 'A', 'B'], '金额': [1, 1, 3]})
        out2, _ = _apply_clean(df2, {'op': 'clean', 'action': 'dedup'})
        with self.subTest(case='全列去重 3→2'):
            self.assertTrue(len(out2) == 2, f'len={len(out2)}')
        out3, _ = _apply_clean(df2, {'op': 'clean', 'action': 'dedup', 'subset': ['业务员']})
        with self.subTest(case='按业务员去重 3→2'):
            self.assertTrue(len(out3) == 2, f'len={len(out3)}')
        out4, _ = _apply_clean(df, {'op': 'clean', 'action': 'filter', 'where': {'col': '金额', 'op': 'gte', 'value': 2}})
        with self.subTest(case='金额≥2 → 2 行'):
            self.assertTrue(len(out4) == 2, f'len={len(out4)}')
        out5, _ = _apply_clean(df, {'op': 'clean', 'action': 'replace', 'where': {'col': '金额', 'op': 'lt', 'value': 2}, 'with': None})
        with self.subTest(case='金额<2 → None'):
            self.assertTrue(pd.isna(out5.loc[0, '金额']), str(out5.loc[0, '金额']))
        dfc = pd.DataFrame({'金额': ['1', '2.5', 'abc']})
        outc, _ = _apply_cast(dfc, {'op': 'cast', 'col': '金额', 'to': 'numeric'})
        with self.subTest(case='文本→数值（abc→NaN）'):
            self.assertTrue(outc['金额'].dtype.kind in 'fiu' and pd.isna(outc.loc[2, '金额']), f"dtype={outc['金额'].dtype}")
        dfd = pd.DataFrame({'日期': ['2024-01-01', '2024-02-01']})
        outd, _ = _apply_cast(dfd, {'op': 'cast', 'col': '日期', 'to': 'date'})
        with self.subTest(case='文本→日期'):
            self.assertTrue(pd.api.types.is_datetime64_any_dtype(outd['日期']), f"dtype={outd['日期'].dtype}")
        dfp = pd.DataFrame({'期间': ['Q1', 'Q1', 'Q2', 'Q2'], '业务员': ['A', 'B', 'A', 'B'], '回款': [100, 200, 150, 250]})
        outp, _ = _apply_pivot(dfp, {'op': 'pivot', 'mode': 'wide', 'index': '期间', 'columns': '业务员', 'values': '回款'})
        with self.subTest(case='长转宽：4 行→2 行'):
            self.assertTrue(len(outp) == 2, f'shape={outp.shape}')
        with self.subTest(case='宽表含 A/B 列'):
            self.assertTrue('A' in outp.columns and 'B' in outp.columns, str(list(outp.columns)))
        with self.subTest(case='A/Q1=100'):
            self.assertTrue(float(outp.loc[outp['期间'] == 'Q1', 'A'].iloc[0]) == 100, str(outp))
        dfw = pd.DataFrame({'期间': ['Q1', 'Q2'], '回款A': [100, 150], '回款B': [200, 250]})
        outl, _ = _apply_pivot(dfw, {'op': 'pivot', 'mode': 'long', 'id_vars': ['期间'], 'var_name': '业务员', 'value_name': '回款'})
        with self.subTest(case='宽转长：2 行→4 行'):
            self.assertTrue(len(outl) == 4, f'shape={outl.shape}')
        with self.subTest(case='长表列名'):
            self.assertTrue(list(outl.columns) == ['期间', '业务员', '回款'], str(list(outl.columns)))
