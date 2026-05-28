#!/usr/bin/env python3
"""
나라장터 RFP 자동 모니터링
──────────────────────────
- 평일 아침 7시 (KST) GitHub Actions 실행
- 입찰공고 + 사전규격공고 수집 (개요·사업기간·링크 포함)
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
BID_LIST_URL  = 'https://apis.data.go.kr/1230000/BidPublicInfoService04/getBidPblancListInfoServc'
BID_DTL_URL   = 'https://apis.data.go.kr/1230000/BidPublicInfoService04/getBidPblancInfoServc04'
PRE_URL       = 'https://apis.data.go.kr/1230000/PrePrddlInfoService/getPrePrddlInfoListServc'
PRE_DTL_URL   = 'https://apis.data.go.kr/1230000/PrePrddlInfoService/getPrePrddlInfoServc'

KST = ZoneInfo('Asia/Seoul')
MAX_DETAIL    = 15   # 상세 조회 최대 건수 (API 부하 방지)


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
# 2. API 호출
# ─────────────────────────────────────────────────────────
def _get(url, params):
    try:
        r = requests.get(url, params=params, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f'  ⚠ API 오류: {e}')
        return {}

def _collect(url, params, bucket, seen, id_key):
    body  = _get(url, params).get('response', {}).get('body', {})
    items = body.get('items', [])
    if isinstance(items, dict):
        items = [items]
    for item in (items or []):
        uid = item.get(id_key, '')
        if uid and uid not in seen:
            seen.add(uid)
            bucket.append(item)


# ─────────────────────────────────────────────────────────
# 3. 입찰공고 수집 + 상세 조회
# ─────────────────────────────────────────────────────────
def fetch_bid_notices(start_dt, end_dt):
    results, seen = [], set()
    base = {
        'serviceKey': DATA_API_KEY,
        'numOfRows': '100', 'pageNo': '1',
        'inqryDiv': '1',
        'inqryBgnDt': start_dt, 'inqryEndDt': end_dt,
        'type': 'json',
    }
    for kw in KEYWORDS:
        _collect(BID_LIST_URL, {**base, 'bidNtceNm': kw}, results, seen, 'bidNtceNo')
    _collect(BID_LIST_URL, {**base, 'dtilPrdlstCd': PRDLST_CD}, results, seen, 'bidNtceNo')

    # 상세 조회 (개요·사업기간)
    for item in results[:MAX_DETAIL]:
        dtl = _get(BID_DTL_URL, {
            'serviceKey': DATA_API_KEY,
            'bidNtceNo':  item.get('bidNtceNo', ''),
            'bidNtceOrd': item.get('bidNtceOrd', '00'),
            'type': 'json',
        }).get('response', {}).get('body', {}).get('items', {})
        if isinstance(dtl, list) and dtl:
            dtl = dtl[0]
        if isinstance(dtl, dict):
            item['_dtl'] = dtl
    return results


# ─────────────────────────────────────────────────────────
# 4. 사전규격공고 수집 + 상세 조회
# ─────────────────────────────────────────────────────────
def fetch_pre_notices(start_dt, end_dt):
    results, seen = [], set()
    base = {
        'serviceKey': DATA_API_KEY,
        'numOfRows': '100', 'pageNo': '1',
        'inqryBgnDt': start_dt[:8],
        'inqryEndDt': end_dt[:8],
        'type': 'json',
    }
    for kw in KEYWORDS:
        _collect(PRE_URL, {**base, 'prdctClsfcNoNm': kw}, results, seen, 'bfSpecRgstNo')
    _collect(PRE_URL, {**base, 'dtilPrdlstCd': PRDLST_CD}, results, seen, 'bfSpecRgstNo')

    # 상세 조회
    for item in results[:MAX_DETAIL]:
        dtl = _get(PRE_DTL_URL, {
            'serviceKey':  DATA_API_KEY,
            'bfSpecRgstNo': item.get('bfSpecRgstNo', ''),
            'type': 'json',
        }).get('response', {}).get('body', {}).get('items', {})
        if isinstance(dtl, list) and dtl:
            dtl = dtl[0]
        if isinstance(dtl, dict):
            item['_dtl'] = dtl
    return results


# ─────────────────────────────────────────────────────────
# 5. 포맷 헬퍼
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

def clip(text, n=120):
    """긴 텍스트 말줄임"""
    if not text: return ''
    text = str(text).replace('\n', ' ').replace('\r', ' ').strip()
    return text[:n] + '…' if len(text) > n else text


# ─────────────────────────────────────────────────────────
# 6. HTML 생성
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
  .card-body{padding:10px 14px;font-size:12px;color:#444;line-height:1.8}
  .meta{display:flex;flex-wrap:wrap;gap:16px;margin-top:6px}
  .meta-item{display:flex;flex-direction:column}
  .meta-label{font-size:10px;color:#999;margin-bottom:2px}
  .meta-value{font-size:12px;color:#222;font-weight:500}
  .badge{display:inline-block;background:#e8f0fe;color:#1a4b8c;
         border-radius:3px;padding:1px 6px;font-size:11px;font-weight:600}
  .empty{color:#999;font-size:12px;padding:12px 0}
  .ftr{background:#f5f6fa;padding:12px 24px;font-size:11px;
       color:#aaa;border-top:1px solid #eee}
  .ovw{color:#555;font-size:12px;margin-top:6px;line-height:1.6;
       border-top:1px solid #eee;padding-top:8px}
</style>
"""

def bid_cards(items):
    if not items:
        return '<p class="empty">해당 기간 공고 없음</p>'
    cards = []
    for it in items:
        dtl      = it.get('_dtl', {})
        name     = it.get('bidNtceNm', '-')
        url      = it.get('bidNtceUrl', '') or dtl.get('bidNtceUrl', '')
        org      = it.get('ntceInsttNm', '-')
        money    = fmt_money(it.get('presmptPrce','') or it.get('asignBdgtAmt','')
                             or dtl.get('presmptPrce',''))
        deadline = fmt_date(it.get('bidClseDt','') or it.get('opengDt','')
                            or dtl.get('bidClseDt',''))
        no       = it.get('bidNtceNo', '-')

        # 개요
        overview = clip(dtl.get('ntcContents','') or dtl.get('dtlContents','')
                        or it.get('ntcContents',''))
        # 사업기간
        period   = (dtl.get('cntrctPerdDt','') or dtl.get('cntrctPrdDt','')
                    or it.get('cntrctPerdDt',''))
        period_str = fmt_date(period) if period else '-'

        title_html = (f'<a href="{url}" target="_blank">{name}</a>' if url else name)

        ovw_html = (f'<div class="ovw">📄 {overview}</div>' if overview else '')

        cards.append(f"""
<div class="card">
  <div class="card-head">
    <span class="card-title">{title_html}</span>
    <span style="font-size:11px;color:#888">{no}</span>
  </div>
  <div class="card-body">
    <div class="meta">
      <div class="meta-item"><span class="meta-label">발주처</span>
        <span class="meta-value">{org}</span></div>
      <div class="meta-item"><span class="meta-label">금액</span>
        <span class="meta-value">{money}</span></div>
      <div class="meta-item"><span class="meta-label">사업기간</span>
        <span class="meta-value">{period_str}</span></div>
      <div class="meta-item"><span class="meta-label">제안서 마감</span>
        <span class="meta-value">{deadline}</span></div>
    </div>
    {ovw_html}
  </div>
</div>""")
    return '\n'.join(cards)


def pre_cards(items):
    if not items:
        return '<p class="empty">해당 기간 공고 없음</p>'
    cards = []
    for it in items:
        dtl      = it.get('_dtl', {})
        name     = it.get('prdctClsfcNoNm','') or it.get('bfSpecRgstNo','-')
        org      = it.get('ntceInsttNm', '-')
        money    = fmt_money(it.get('totPrce','') or it.get('asignBdgtAmt','')
                             or dtl.get('totPrce',''))
        deadline = fmt_date(it.get('opninRcptDdlnDt','') or it.get('rgstDt','')
                            or dtl.get('opninRcptDdlnDt',''))
        no       = it.get('bfSpecRgstNo', '-')
        overview = clip(dtl.get('bfSpecContents','') or dtl.get('dtlContents','')
                        or it.get('bfSpecContents',''))
        period   = (dtl.get('cntrctPerdDt','') or dtl.get('cntrctPrdDt','')
                    or it.get('cntrctPerdDt',''))
        period_str = fmt_date(period) if period else '-'
        ovw_html = (f'<div class="ovw">📄 {overview}</div>' if overview else '')

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
      <div class="meta-item"><span class="meta-label">금액</span>
        <span class="meta-value">{money}</span></div>
      <div class="meta-item"><span class="meta-label">사업기간</span>
        <span class="meta-value">{period_str}</span></div>
      <div class="meta-item"><span class="meta-label">의견마감</span>
        <span class="meta-value">{deadline}</span></div>
    </div>
    {ovw_html}
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
    <div class="sec">① 입찰공고 <span class="badge">{len(bid_list)}건</span></div>
    {bid_cards(bid_list)}
    <div class="sec">② 사전규격공고 <span class="badge">{len(pre_list)}건</span></div>
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
# 7. 이메일 발송
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
# 8. 메인
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

    print(f'  입찰공고 {len(bid_list)}건 / 사전규격공고 {len(pre_list)}건 (총 {total}건)')

    if total == 0:
        subject   = f'[나라장터] {today_str} 신규 공고 없음'
        html_body = make_empty_html(today_str)
    else:
        subject   = f'[나라장터] {today_str} RFP 현황 — 총 {total}건'
        html_body = make_html(bid_list, pre_list, today_str, start_dt, end_dt)

    send_email(subject, html_body)


if __name__ == '__main__':
    main()
