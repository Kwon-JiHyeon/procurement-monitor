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

# ── 정확한 API 엔드포인트 (공식 문서 기준) ──────────────
BID_URL = 'http://apis.data.go.kr/1230000/ad/BidPublicInfoService/getBidPblancListInfoServc'
PRE_URL = 'http://apis.data.go.kr/1230000/ao/HrcspSsstndrdInfoService/getPublicPrcureThngInfoServc'

KST = ZoneInfo('Asia/Seoul')


# ─────────────────────────────────────────────────────────
# 1. 날짜 범위
# ─────────────────────────────────────────────────────────
def get_date_range():
    today = datetime.now(KST)
    days_back = 3 if today.weekday() == 0 else 1
    start = today - timedelta(days=days_back)
    return (
        start.strftime('%Y%m%d') + '0000',
        today.strftime('%Y%m%d') + '2359',
    )


# ─────────────────────────────────────────────────────────
# 2. 키워드 매칭 (공고명에 키워드 포함 여부)
# ─────────────────────────────────────────────────────────
def match_keywords(text):
    text_upper = str(text or '').upper()
    for kw in KEYWORDS:
        if kw.upper() in text_upper:
            return True
    return False

def match_prdlst(prdlst_str):
    """세부품명번호 문자열에 PRDLST_CD 포함 여부"""
    return PRDLST_CD in str(prdlst_str or '')


# ─────────────────────────────────────────────────────────
# 3. API 호출
# ─────────────────────────────────────────────────────────
def _get(url, params):
    try:
        r = requests.get(url, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        body = data.get('response', {}).get('body', {})
        items = body.get('items', [])
        if isinstance(items, dict):
            items = [items]
        return items or []
    except Exception as e:
        print(f'  ⚠ API 오류 ({url}): {e}')
        return []


# ─────────────────────────────────────────────────────────
# 4. 입찰공고 수집 (전체 조회 후 Python 필터링)
# ─────────────────────────────────────────────────────────
def fetch_bid_notices(start_dt, end_dt):
    params = {
        'ServiceKey': DATA_API_KEY,
        'numOfRows':  '200',
        'pageNo':     '1',
        'inqryDiv':   '1',
        'inqryBgnDt': start_dt,
        'inqryEndDt': end_dt,
        'type':       'json',
    }
    items = _get(BID_URL, params)

    # Python에서 키워드 필터링
    results, seen = [], set()
    for item in items:
        name      = item.get('bidNtceNm', '')
        prdlst    = item.get('prdctDtlList', '')
        uid       = item.get('bidNtceNo', '')
        if uid and uid not in seen:
            if match_keywords(name) or match_prdlst(prdlst):
                seen.add(uid)
                results.append(item)

    print(f'  입찰공고 전체 {len(items)}건 중 필터링 후 {len(results)}건')
    return results


# ─────────────────────────────────────────────────────────
# 5. 사전규격공고 수집
# ─────────────────────────────────────────────────────────
def fetch_pre_notices(start_dt, end_dt):
    params = {
        'ServiceKey': DATA_API_KEY,
        'numOfRows':  '200',
        'pageNo':     '1',
        'inqryDiv':   '1',
        'inqryBgnDt': start_dt,
        'inqryEndDt': end_dt,
        'type':       'json',
    }
    items = _get(PRE_URL, params)

    # Python에서 키워드 필터링 (품명 + 세부품명번호)
    results, seen = [], set()
    for item in items:
        name   = item.get('prdctClsfcNoNm', '')
        prdlst = item.get('prdctDtlList', '')
        uid    = item.get('bfSpecRgstNo', '')
        if uid and uid not in seen:
            if match_keywords(name) or match_prdlst(prdlst):
                seen.add(uid)
                results.append(item)

    print(f'  사전규격공고 전체 {len(items)}건 중 필터링 후 {len(results)}건')
    return results


# ─────────────────────────────────────────────────────────
# 6. 포맷 헬퍼
# ─────────────────────────────────────────────────────────
def fmt_money(val):
    try:
        n = float(str(val).replace(',', ''))
        if n >= 1e8:  return f'{n/1e8:.1f}억원'
        if n >= 1e4:  return f'{n/1e4:.0f}만원'
        return f'{int(n):,}원'
    except Exception:
        return val or '-'

def fmt_date(val):
    s = str(val or '').replace('-','').replace(':','').replace(' ','')
    if len(s) >= 12: return f"{s[:4]}-{s[4:6]}-{s[6:8]} {s[8:10]}:{s[10:12]}"
    if len(s) >= 8:  return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return val or '-'


# ─────────────────────────────────────────────────────────
# 7. HTML 생성
# ─────────────────────────────────────────────────────────
STYLE = """
<style>
  body{font-family:'맑은고딕',Arial,sans-serif;background:#f5f6fa;margin:0;padding:20px}
  .wrap{max-width:760px;margin:0 auto;background:#fff;border-radius:8px;
        box-shadow:0 2px 8px rgba(0,0,0,.08);overflow:hidden}
  .hdr{background:#1a4b8c;color:#fff;padding:20px 24px}
  .hdr h2{margin:0;font-size:16px;font-weight:600}
  .hdr p{margin:4px 0 0;font-size:12px;opacity:.8}
  .body{padding:16px 24px}
  .sec{font-size:13px;font-weight:700;color:#1a4b8c;
       border-left:4px solid #1a4b8c;padding-left:8px;margin:20px 0 10px}
  .card{border:1px solid #e8eaf0;border-radius:6px;margin-bottom:12px;overflow:hidden}
  .card-head{background:#f0f4ff;padding:10px 14px;display:flex;
             justify-content:space-between;align-items:center;flex-wrap:wrap;gap:6px}
  .card-title{font-size:13px;font-weight:600;color:#1a4b8c}
  .card-title a{color:#1a4b8c;text-decoration:none}
  .card-title a:hover{text-decoration:underline}
  .card-body{padding:10px 14px}
  .meta{display:flex;flex-wrap:wrap;gap:16px}
  .meta-item{display:flex;flex-direction:column}
  .meta-label{font-size:10px;color:#999;margin-bottom:2px}
  .meta-value{font-size:12px;color:#222;font-weight:500}
  .badge{display:inline-block;background:#e8f0fe;color:#1a4b8c;
         border-radius:3px;padding:1px 8px;font-size:11px;margin-left:6px}
  .empty{color:#999;font-size:12px;padding:12px 0}
  .ftr{background:#f5f6fa;padding:12px 24px;font-size:11px;
       color:#aaa;border-top:1px solid #eee}
</style>
"""

def bid_cards(items):
    if not items:
        return '<p class="empty">해당 기간 공고 없음</p>'
    cards = []
    for it in items:
        name     = it.get('bidNtceNm', '-')
        url      = it.get('ntceSpecDocUrl1', '') or it.get('bidNtceUrl', '')
        org      = it.get('ntceInsttNm', '-')
        money    = fmt_money(it.get('presmptPrce', '') or it.get('asignBdgtAmt', ''))
        deadline = fmt_date(it.get('bidClseDt', '') or it.get('opengDt', ''))
        no       = it.get('bidNtceNo', '-')
        g2b_url  = f"https://www.g2b.go.kr:8101/ep/invitation/publish/bidInfoDtl.do?bidno={no}&bidseq=000"
        title    = f'<a href="{g2b_url}" target="_blank">{name}</a>'
        cards.append(f"""
<div class="card">
  <div class="card-head">
    <span class="card-title">{title}</span>
    <span style="font-size:11px;color:#888">{no}</span>
  </div>
  <div class="card-body">
    <div class="meta">
      <div class="meta-item"><span class="meta-label">발주처</span>
        <span class="meta-value">{org}</span></div>
      <div class="meta-item"><span class="meta-label">금액(추정)</span>
        <span class="meta-value">{fmt_money(it.get('presmptPrce',''))}</span></div>
      <div class="meta-item"><span class="meta-label">입찰마감</span>
        <span class="meta-value">{deadline}</span></div>
    </div>
  </div>
</div>""")
    return '\n'.join(cards)


def pre_cards(items):
    if not items:
        return '<p class="empty">해당 기간 공고 없음</p>'
    cards = []
    for it in items:
        name     = it.get('prdctClsfcNoNm', '-')
        org      = it.get('orderInsttNm', '-')
        money    = fmt_money(it.get('asignBdgtAmt', ''))
        deadline = fmt_date(it.get('opninRgstClseDt', ''))
        no       = it.get('bfSpecRgstNo', '-')
        cards.append(f"""
<div class="card">
  <div class="card-head">
    <span class="card-title">{name}</span>
    <span style="font-size:11px;color:#888">{no}</span>
  </div>
  <div class="card-body">
    <div class="meta">
      <div class="meta-item"><span class="meta-label">발주처</span>
        <span class="meta-value">{org}</span></div>
      <div class="meta-item"><span class="meta-label">배정예산</span>
        <span class="meta-value">{money}</span></div>
      <div class="meta-item"><span class="meta-label">의견마감</span>
        <span class="meta-value">{deadline}</span></div>
    </div>
  </div>
</div>""")
    return '\n'.join(cards)


def make_html(bid_list, pre_list, today_str, start_dt, end_dt):
    total  = len(bid_list) + len(pre_list)
    period = (f"{start_dt[:4]}-{start_dt[4:6]}-{start_dt[6:8]} ~ "
              f"{end_dt[:4]}-{end_dt[4:6]}-{end_dt[6:8]}")
    return f"""<!DOCTYPE html><html lang="ko">
<head><meta charset="UTF-8">{STYLE}</head>
<body><div class="wrap">
  <div class="hdr">
    <h2>📋 나라장터 RFP 현황</h2>
    <p>{today_str} &nbsp;|&nbsp; 총 <strong>{total}건</strong>
       &nbsp;|&nbsp; 조회기간: {period}</p>
  </div>
  <div class="body">
    <div class="sec">① 입찰공고<span class="badge">{len(bid_list)}건</span></div>
    {bid_cards(bid_list)}
    <div class="sec">② 사전규격공고<span class="badge">{len(pre_list)}건</span></div>
    {pre_cards(pre_list)}
  </div>
  <div class="ftr">
    검색 키워드: ISP · ISMP · 정보화전략 &nbsp;|&nbsp;
    세부품명번호: {PRDLST_CD} &nbsp;|&nbsp; 자동발송 (GitHub Actions)
  </div>
</div></body></html>"""


def make_empty_html(today_str):
    return f"""<!DOCTYPE html><html lang="ko">
<head><meta charset="UTF-8">{STYLE}</head>
<body><div class="wrap">
  <div class="hdr"><h2>📋 나라장터 RFP 현황</h2><p>{today_str}</p></div>
  <div class="body">
    <p class="empty" style="padding:20px 0">해당 기간 내 신규 공고가 없습니다.</p>
  </div>
  <div class="ftr">
    검색 키워드: ISP · ISMP · 정보화전략 &nbsp;|&nbsp; 세부품명번호: {PRDLST_CD}
  </div>
</div></body></html>"""


# ─────────────────────────────────────────────────────────
# 8. 이메일 발송
# ─────────────────────────────────────────────────────────
def send_email(subject, html_body):
    recipients = [r.strip() for r in MAIL_TO.split(',')]
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From']    = MAIL_FROM
    msg['To']      = ', '.join(recipients)
    msg.attach(MIMEText(html_body, 'html', 'utf-8'))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as srv:
        srv.ehlo()
        srv.starttls()
        srv.login(SMTP_USER, SMTP_PASS)
        srv.sendmail(MAIL_FROM, recipients, msg.as_string())

    print(f'✅ 이메일 발송 완료 → {recipients}')


# ─────────────────────────────────────────────────────────
# 9. 메인
# ─────────────────────────────────────────────────────────
def main():
    today     = datetime.now(KST)
    today_str = today.strftime('%Y년 %m월 %d일 (%a)')
    start_dt, end_dt = get_date_range()

    print(f'[{today_str}] 나라장터 모니터링 시작')
    print(f'  조회 기간: {start_dt} ~ {end_dt}')

    bid_list = fetch_bid_notices(start_dt, end_dt)
    pre_list = fetch_pre_notices(start_dt, end_dt)
    total    = len(bid_list) + len(pre_list)

    print(f'  최종: 입찰공고 {len(bid_list)}건 / 사전규격공고 {len(pre_list)}건 (총 {total}건)')

    if total == 0:
        subject   = f'[나라장터] {today_str} 신규 공고 없음'
        html_body = make_empty_html(today_str)
    else:
        subject   = f'[나라장터] {today_str} RFP 현황 — 총 {total}건'
        html_body = make_html(bid_list, pre_list, today_str, start_dt, end_dt)

    send_email(subject, html_body)


if __name__ == '__main__':
    main()
