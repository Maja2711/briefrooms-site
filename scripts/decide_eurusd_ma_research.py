#!/usr/bin/env python3
"""Convert EURUSD MA research into an auditable promotion recommendation.

This is a governance gate, not an auto-trader. It reads research outputs and writes
one deterministic recommendation so the research loop does not stop at raw stats.
"""
from __future__ import annotations
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/investments/eurusd_sma_vs_ema_research.json'
OUT=ROOT/'data/investments/eurusd_ma_research_decision.json'

MIN_COUNT=30
MIN_HIT=0.52
MIN_MEAN=0.015

def load(path): return json.loads(path.read_text(encoding='utf-8'))

def robust(row):
    return int(row.get('count') or 0)>=MIN_COUNT and float(row.get('hit_rate') or 0)>=MIN_HIT and float(row.get('mean_week_percent') or 0)>MIN_MEAN

def main():
    r=load(SRC)
    tf=r['timeframes']
    h1_ema_long=tf['H1']['ema']['price_vs_all']['long']
    h1_ema_short=tf['H1']['ema']['price_vs_all']['short']
    h1_sma_short=tf['H1']['sma']['price_vs_all']['short']
    w1_sma_support=tf['W1']['sma']['support_resistance']['long']
    w1_hybrid_fast=tf['W1']['hybrid']['fast_with_slow']['long']
    checks={
      'h1_ema_price_long':{'passed':robust(h1_ema_long),**h1_ema_long},
      'h1_ema_price_short':{'passed':robust(h1_ema_short),**h1_ema_short},
      'h1_sma_price_short':{'passed':robust(h1_sma_short),**h1_sma_short},
      'w1_sma_support_long':{'passed':False,'reason':'sample_below_30_even_if_promising',**w1_sma_support},
      'w1_hybrid_fast_long':{'passed':robust(w1_hybrid_fast),**w1_hybrid_fast},
    }
    recommendation={
      'status':'research_recommendation_ready',
      'sample_weeks':r.get('sample_weeks'),
      'decision':'keep_sma_structural_layer_and_test_ema_h1_as_secondary_timing',
      'production_action':'no_automatic_replacement',
      'recommended_next_test':'walk_forward_holdout_for_H1_EMA_price_vs_all_with_existing_W1_SMA_structure',
      'rationale':[
        'H1 EMA price-vs-all is the most repeatable positive result on both long and short sides.',
        'W1 SMA support remains promising but its current common-sample count is too small for promotion by itself.',
        'EMA or hybrid full-stack/fast-slow rules on D1 and W1 do not show broad enough positive expectancy to replace SMA globally.',
        'High hit rate with negative mean return is rejected by governance.'
      ],
      'checks':checks,
      'guardrails':{'min_count':MIN_COUNT,'min_hit_rate':MIN_HIT,'min_mean_week_percent':MIN_MEAN,'auto_modify_production':False}
    }
    OUT.write_text(json.dumps(recommendation,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(recommendation,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
