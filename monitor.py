#!/usr/bin/env python3
"""
나라장터 RFP 자동 모니터링
──────────────────────────
- 평일 아침 7시 (KST) GitHub Actions 실행
- 입찰공고 + 사전규격공고 수집 후 키워드 필터링
- 회사 SMTP로 이메일 발송
"""

import os
import smtplib
import requests
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

# ── 환경변수 (GitHub Secrets) ─────────────────────────────
DATA_API_KEY = os.environ['DATA_GO_API_KEY']
SMTP_HOST    = os.environ['SMTP_HOST']
SMTP_PORT    = int(os.environ.get('SMTP_PORT', '587'))
SMTP_USER    = os.environ['SMTP_USER']
SMTP_PASS    = os.environ['SMTP_PASSWORD']
MAIL_FROM    = os.environ['MAIL_FROM']
MAIL_TO      = os.environ['MAIL_TO']

# ── 검색 조건 ─────────────────────────────────────────────
KEYWORDS  = ['ISP', 'ISMP', '정보화전략']
PRDLST_CD = '8010150701'

# ── API 엔드포인트 ────────────────────────────────────────
BID_URL = 'http://apis.data.go.kr/1230000/ad/BidPublicInfoService/getBidPblancListInfoServc'
PRE_URL = 'http://apis.data.go.kr/1230000/ao/HrcspSsstndrdInfoService/getPublicPrcureThngInfoServc'

KST = ZoneInfo('Asia/Seoul')


def get_date_range():
    today = datetime.now(KST)
    days_back = 3 if today.weekday() == 0 else 1
    start = today - timedelta(days=days_back)
    return (
        start.strftime('%Y%m%d') + '0000',
        today.strftime('%Y%m%d') + '2359',
    )

def match_keywords(text):
    t = str(text or '').upper()
    return any(kw.upper() in t for kw in KEYWORDS)

def match_prdlst(s):
    return PRDLST_CD in str(s or '')

def _get(url, params):
    try:
        r = requests.get(url, params=params, timeout=30)
        r.raise_for_status()
        body  = r.json().get('response', {}).get('body', {})
        items = body.get('items', [])
        if isinstance(items, dict):
            items = [items]
        return items or []
    except Exception as e:
        print(f'  ⚠ API 오류: {e}')
        return []

def fetch_bid_notices(start_dt, end_dt):
    items = _get(BID_URL, {
        'ServiceKey': DATA_API_KEY, 'numOfRows': '200', 'pageNo': '1',
        'inqryDiv': '1', 'inqryBgnDt': start_dt, 'inqryEndDt': end_dt, 'type': 'json',
    })
    results, seen = [], set()
    for it in items:
        uid = it.get('bidNtceNo', '')
        if uid and uid not in seen:
            if match_keywords(it.get('bidNtceNm','')) or match_prdlst(it.get('prdctDtlList','')):
                seen.add(uid); results.append(it)
    print(f'  입찰공고 전체 {len(items)}건 → 필터 {len(results)}건')
    return results

def fetch_pre_notices(start_dt, end_dt):
    items = _get(PRE_URL, {
        'ServiceKey': DATA_API_KEY, 'numOfRows': '200', 'pageNo': '1',
        'inqryDiv': '1', 'inqryBgnDt': start_dt, 'inqryEndDt': end_dt, 'type': 'json',
    })
    results, seen = [], set()
    for it in items:
        uid = it.get('bfSpecRgstNo', '')
        if uid and uid not in seen:
            if match_keywords(it.get('prdctClsfcNoNm','')) or match_prdlst(it.get('prdctDtlList','')):
                seen.add(uid); results.append(it)
    print(f'  사전규격 전체 {len(items)}건 → 필터 {len(results)}건')
    return results

def fmt_money(val):
    try:
        n = float(str(val).replace(',',''))
        if n >= 1e8: return f'{n/1e8:.1f}억원'
        if n >= 1e4: return f'{n/1e4:.0f}만원'
        return f'{int(n):,}원'
    except: return val or '-'

def fmt_date(val):
    s = str(val or '').replace('-','').replace(':','').replace(' ','')
    if len(s) >= 12: return f"{s[:4]}.{s[4:6]}.{s[6:8]} {s[8:10]}:{s[10:12]}"
    if len(s) >= 8:  return f"{s[:4]}.{s[4:6]}.{s[6:8]}"
    return val or '-'

# ── HTML 스타일 ───────────────────────────────────────────
STYLE = """
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:#EAECF0;font-family:'맑은 고딕','Malgun Gothic',Arial,sans-serif;padding:24px 16px}
  .outer{max-width:680px;margin:0 auto}

  /* 헤더 */
  .hdr{background:#1B3F7A;border-radius:12px 12px 0 0;padding:24px 28px}
  .hdr-top{display:flex;align-items:center;gap:10px;margin-bottom:6px}
  .hdr-icon{width:32px;height:32px;background:rgba(255,255,255,.15);
            border-radius:8px;display:flex;align-items:center;
            justify-content:center;font-size:16px}
  .hdr-title{color:#fff;font-size:17px;font-weight:700;letter-spacing:-.3px}
  .hdr-sub{color:rgba(255,255,255,.65);font-size:12px;margin-top:2px}
  .hdr-stats{display:flex;gap:8px;margin-top:14px;flex-wrap:wrap}
  .stat-pill{background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.2);
             color:#fff;font-size:11px;padding:4px 12px;border-radius:20px}
  .stat-pill b{font-weight:700}

  /* 본문 */
  .body{background:#fff;padding:24px 28px}

  /* 섹션 헤더 */
  .sec-hdr{display:flex;align-items:center;gap:10px;margin:0 0 14px}
  .sec-dot{width:4px;height:20px;border-radius:2px;background:#1B3F7A}
  .sec-label{font-size:13px;font-weight:700;color:#1B3F7A}
  .sec-count{background:#EBF0FB;color:#1B3F7A;font-size:11px;
             font-weight:700;padding:2px 9px;border-radius:20px}
  .sec-divider{height:1px;background:#F0F2F5;margin:20px 0}

  /* 카드 */
  .card{border:1.5px solid #E8ECF4;border-radius:10px;
        margin-bottom:10px;overflow:hidden;transition:border-color .15s}
  .card:last-child{margin-bottom:0}
  .card-top{padding:13px 16px 11px;border-bottom:1px solid #F0F2F5}
  .card-name{font-size:13px;font-weight:700;color:#1B3F7A;
             line-height:1.45;margin-bottom:4px}
  .card-name a{color:#1B3F7A;text-decoration:none}
  .card-name a:hover{text-decoration:underline}
  .card-no{font-size:11px;color:#A0A8B8;letter-spacing:.3px}
  .card-meta{padding:11px 16px;display:flex;flex-wrap:wrap;gap:0}
  .meta-cell{flex:1;min-width:120px;padding:4px 8px 4px 0;
             border-right:1px solid #F0F2F5}
  .meta-cell:last-child{border-right:none}
  .meta-cell:first-child{padding-left:0}
  .meta-key{font-size:10px;color:#A0A8B8;font-weight:600;
            text-transform:uppercase;letter-spacing:.5px;margin-bottom:3px}
  .meta-val{font-size:12px;color:#1E2A3A;font-weight:600}

  /* 빈 상태 */
  .empty-box{background:#F8F9FC;border:1.5px dashed #DDE2EC;
             border-radius:8px;padding:18px;text-align:center;
             color:#A0A8B8;font-size:12px}

  /* 푸터 */
  .ftr{background:#F5F7FB;border-radius:0 0 12px 12px;
       padding:14px 28px;border-top:1px solid #E8ECF4}
  .ftr-text{font-size:10.5px;color:#A0A8B8;line-height:1.6}
  .ftr-text b{color:#7A8BA8;font-weight:600}
</style>
"""

def card_bid(it):
    name     = it.get('bidNtceNm', '-')
    no       = it.get('bidNtceNo', '-')
    org      = it.get('ntceInsttNm', '-')
    money    = fmt_money(it.get('presmptPrce','') or it.get('asignBdgtAmt',''))
    deadline = fmt_date(it.get('bidClseDt','') or it.get('opengDt',''))
    link     = f"https://www.g2b.go.kr:8101/ep/invitation/publish/bidInfoDtl.do?bidno={no}&bidseq=000"
    return f"""
<div class="card">
  <div class="card-top">
    <div class="card-name"><a href="{link}" target="_blank">{name}</a></div>
    <div class="card-no">{no}</div>
  </div>
  <div class="card-meta">
    <div class="meta-cell">
      <div class="meta-key">발주처</div>
      <div class="meta-val">{org}</div>
    </div>
    <div class="meta-cell">
      <div class="meta-key">추정금액</div>
      <div class="meta-val">{money}</div>
    </div>
    <div class="meta-cell">
      <div class="meta-key">입찰마감</div>
      <div class="meta-val">{deadline}</div>
    </div>
  </div>
</div>"""

def card_pre(it):
    name     = it.get('prdctClsfcNoNm', '-')
    no       = it.get('bfSpecRgstNo', '-')
    org      = it.get('orderInsttNm', '-')
    money    = fmt_money(it.get('asignBdgtAmt',''))
    deadline = fmt_date(it.get('opninRgstClseDt',''))
    return f"""
<div class="card">
  <div class="card-top">
    <div class="card-name">{name}</div>
    <div class="card-no">{no}</div>
  </div>
  <div class="card-meta">
    <div class="meta-cell">
      <div class="meta-key">발주처</div>
      <div class="meta-val">{org}</div>
    </div>
    <div class="meta-cell">
      <div class="meta-key">배정예산</div>
      <div class="meta-val">{money}</div>
    </div>
    <div class="meta-cell">
      <div class="meta-key">의견마감</div>
      <div class="meta-val">{deadline}</div>
    </div>
  </div>
</div>"""

def make_html(bid_list, pre_list, today_str, start_dt, end_dt):
    total  = len(bid_list) + len(pre_list)
    period = f"{start_dt[:4]}.{start_dt[4:6]}.{start_dt[6:8]} ~ {end_dt[:4]}.{end_dt[4:6]}.{end_dt[6:8]}"

    bid_cards = ''.join(card_bid(it) for it in bid_list) if bid_list else \
        '<div class="empty-box">해당 기간 입찰공고 없음</div>'
    pre_cards = ''.join(card_pre(it) for it in pre_list) if pre_list else \
        '<div class="empty-box">해당 기간 사전규격공고 없음</div>'

    return f"""<!DOCTYPE html><html lang="ko">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width">{STYLE}</head>
<body><div class="outer">
  <div class="hdr">
    <div class="hdr-top">
      <div class="hdr-icon">📋</div>
      <div class="hdr-title">나라장터 RFP 현황</div>
    </div>
    <div class="hdr-sub">{today_str} &nbsp;·&nbsp; 조회기간 {period}</div>
    <div class="hdr-stats">
      <div class="stat-pill">총 <b>{total}건</b></div>
      <div class="stat-pill">입찰공고 <b>{len(bid_list)}건</b></div>
      <div class="stat-pill">사전규격 <b>{len(pre_list)}건</b></div>
    </div>
  </div>

  <div class="body">
    <div class="sec-hdr">
      <div class="sec-dot"></div>
      <div class="sec-label">입찰공고</div>
      <div class="sec-count">{len(bid_list)}건</div>
    </div>
    {bid_cards}

    <div class="sec-divider"></div>

    <div class="sec-hdr">
      <div class="sec-dot"></div>
      <div class="sec-label">사전규격공고</div>
      <div class="sec-count">{len(pre_list)}건</div>
    </div>
    {pre_cards}
  </div>

  <div class="ftr">
    <div class="ftr-text">
      <b>검색 키워드</b> ISP · ISMP · 정보화전략 &nbsp;|&nbsp;
      <b>세부품명번호</b> {PRDLST_CD} &nbsp;|&nbsp;
      자동발송 via GitHub Actions
    </div>
  </div>
</div></body></html>"""

def make_empty_html(today_str):
    return f"""<!DOCTYPE html><html lang="ko">
<head><meta charset="UTF-8">{STYLE}</head>
<body><div class="outer">
  <div class="hdr">
    <div class="hdr-top">
      <div class="hdr-icon">📋</div>
      <div class="hdr-title">나라장터 RFP 현황</div>
    </div>
    <div class="hdr-sub">{today_str}</div>
    <div class="hdr-stats"><div class="stat-pill">신규 공고 없음</div></div>
  </div>
  <div class="body">
    <div class="empty-box" style="padding:32px">
      해당 기간 내 신규 공고가 없습니다.
    </div>
  </div>
  <div class="ftr">
    <div class="ftr-text">
      <b>검색 키워드</b> ISP · ISMP · 정보화전략 &nbsp;|&nbsp;
      <b>세부품명번호</b> {PRDLST_CD}
    </div>
  </div>
</div></body></html>"""

def send_email(subject, html_body):
    recipients = [r.strip() for r in MAIL_TO.split(',')]
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From']    = MAIL_FROM
    msg['To']      = ', '.join(recipients)
    msg.attach(MIMEText(html_body, 'html', 'utf-8'))
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as srv:
        srv.ehlo(); srv.starttls()
        srv.login(SMTP_USER, SMTP_PASS)
        srv.sendmail(MAIL_FROM, recipients, msg.as_string())
    print(f'✅ 이메일 발송 완료 → {recipients}')

def main():
    today     = datetime.now(KST)
    today_str = today.strftime('%Y년 %m월 %d일 (%a)')
    start_dt, end_dt = get_date_range()
    print(f'[{today_str}] 나라장터 모니터링 시작')
    print(f'  조회 기간: {start_dt} ~ {end_dt}')
    bid_list = fetch_bid_notices(start_dt, end_dt)
    pre_list = fetch_pre_notices(start_dt, end_dt)
    total    = len(bid_list) + len(pre_list)
    print(f'  최종: 입찰공고 {len(bid_list)}건 / 사전규격 {len(pre_list)}건 (총 {total}건)')
    if total == 0:
        subject   = f'[나라장터] {today_str} 신규 공고 없음'
        html_body = make_empty_html(today_str)
    else:
        subject   = f'[나라장터] {today_str} RFP 현황 — 총 {total}건'
        html_body = make_html(bid_list, pre_list, today_str, start_dt, end_dt)
    send_email(subject, html_body)

if __name__ == '__main__':
    main()
