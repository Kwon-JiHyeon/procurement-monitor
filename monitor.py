#!/usr/bin/env python3
"""
나라장터 RFP 자동 모니터링
──────────────────────────
- 평일 아침 7시 (KST) GitHub Actions 실행
- 입찰공고 + 사전규격공고 수집
- Python으로 HTML 테이블 직접 생성 (외부 AI API 불필요)
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
BID_URL = 'https://apis.data.go.kr/1230000/BidPublicInfoService04/getBidPblancListInfoServc'
PRE_URL = 'https://apis.data.go.kr/1230000/PrePrddlInfoService/getPrePrddlInfoListServc'

KST = ZoneInfo('Asia/Seoul')


# ─────────────────────────────────────────────────────────
# 1. 날짜 범위
# ─────────────────────────────────────────────────────────
def get_date_range():
    today = datetime.now(KST)
    days_back = 3 if today.weekday() == 0 else 1  # 월요일 → 금요일부터
    start = today - timedelta(days=days_back)
    return (
        start.strftime('%Y%m%d') + '0000',
        today.strftime('%Y%m%d') + '2359',
    )


# ─────────────────────────────────────────────────────────
# 2. API 호출
# ─────────────────────────────────────────────────────────
def _call(url, params, bucket, seen, id_key):
    try:
        r = requests.get(url, params=params, timeout=30)
        r.raise_for_status()
        body  = r.json().get('response', {}).get('body', {})
        items = body.get('items', [])
        if isinstance(items, dict):
            items = [items]
        for item in (items or []):
            uid = item.get(id_key, '')
            if uid and uid not in seen:
                seen.add(uid)
                bucket.append(item)
    except Exception as e:
        print(f'  ⚠ API 오류: {e}')


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
        _call(BID_URL, {**base, 'bidNtceNm': kw}, results, seen, 'bidNtceNo')
    _call(BID_URL, {**base, 'dtilPrdlstCd': PRDLST_CD}, results, seen, 'bidNtceNo')
    return results


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
        _call(PRE_URL, {**base, 'prdctClsfcNoNm': kw}, results, seen, 'prePrddlNo')
    _call(PRE_URL, {**base, 'dtilPrdlstCd': PRDLST_CD}, results, seen, 'prePrddlNo')
    return results


# ─────────────────────────────────────────────────────────
# 3. HTML 생성 (Python 직접 생성, AI API 없음)
# ─────────────────────────────────────────────────────────
STYLE = """
<style>
  body { font-family: '맑은고딕', Arial, sans-serif; background:#f5f6fa; margin:0; padding:20px; }
  .wrap { max-width:720px; margin:0 auto; background:#fff; border-radius:8px;
          box-shadow:0 2px 8px rgba(0,0,0,.08); overflow:hidden; }
  .header { background:#1a4b8c; color:#fff; padding:20px 24px; }
  .header h2 { margin:0; font-size:16px; font-weight:600; }
  .header p  { margin:4px 0 0; font-size:12px; opacity:.8; }
  .body { padding:20px 24px; }
  .section-title { font-size:13px; font-weight:700; color:#1a4b8c;
                   border-left:4px solid #1a4b8c; padding-left:8px;
                   margin:20px 0 10px; }
  table { width:100%; border-collapse:collapse; font-size:12px; }
  th { background:#1a4b8c; color:#fff; padding:8px 10px; text-align:left; }
  td { padding:8px 10px; border-bottom:1px solid #eee; vertical-align:top; }
  tr:last-child td { border-bottom:none; }
  tr:hover td { background:#f0f4ff; }
  a { color:#1a4b8c; text-decoration:none; }
  a:hover { text-decoration:underline; }
  .empty { color:#999; font-size:12px; padding:12px 0; }
  .footer { background:#f5f6fa; padding:12px 24px;
            font-size:11px; color:#aaa; border-top:1px solid #eee; }
  .badge { display:inline-block; background:#e8f0fe; color:#1a4b8c;
           border-radius:3px; padding:1px 6px; font-size:11px; font-weight:600; }
</style>
"""

def fmt_money(val):
    """숫자를 억원 단위로 포맷"""
    try:
        n = float(str(val).replace(',', ''))
        if n >= 1e8:
            return f'{n/1e8:.1f}억원'
        if n >= 1e4:
            return f'{n/1e4:.0f}만원'
        return f'{int(n):,}원'
    except Exception:
        return val or '-'

def fmt_date(val):
    """날짜 문자열 포맷 (YYYYMMDDhhmm → YYYY-MM-DD HH:MM)"""
    s = str(val or '').replace('-', '').replace(':', '').replace(' ', '')
    if len(s) >= 12:
        return f"{s[:4]}-{s[4:6]}-{s[6:8]} {s[8:10]}:{s[10:12]}"
    if len(s) >= 8:
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return val or '-'

def bid_rows(items):
    if not items:
        return '<tr><td colspan="5" class="empty">해당 기간 공고 없음</td></tr>'
    rows = []
    for it in items:
        name    = it.get('bidNtceNm', '-')
        url     = it.get('bidNtceUrl', '')
        org     = it.get('ntceInsttNm', '-')
        money   = fmt_money(it.get('presmptPrce', '') or it.get('asignBdgtAmt', ''))
        deadline= fmt_date(it.get('bidClseDt', '') or it.get('opengDt', ''))
        no      = it.get('bidNtceNo', '-')
        title   = f'<a href="{url}" target="_blank">{name}</a>' if url else name
        rows.append(
            f'<tr><td>{title}</td><td>{org}</td>'
            f'<td style="white-space:nowrap">{money}</td>'
            f'<td style="white-space:nowrap">{deadline}</td>'
            f'<td style="color:#888">{no}</td></tr>'
        )
    return '\n'.join(rows)

def pre_rows(items):
    if not items:
        return '<tr><td colspan="4" class="empty">해당 기간 공고 없음</td></tr>'
    rows = []
    for it in items:
        name    = it.get('prdctClsfcNoNm', '') or it.get('bfSpecRgstNo', '-')
        org     = it.get('ntceInsttNm', '-')
        money   = fmt_money(it.get('totPrce', '') or it.get('asignBdgtAmt', ''))
        deadline= fmt_date(it.get('opninRcptDdlnDt', '') or it.get('rgstDt', ''))
        no      = it.get('bfSpecRgstNo', '-')
        rows.append(
            f'<tr><td>{name}</td><td>{org}</td>'
            f'<td style="white-space:nowrap">{money}</td>'
            f'<td style="white-space:nowrap">{deadline}</td></tr>'
        )
    return '\n'.join(rows)

def make_html(bid_list, pre_list, today_str, start_dt, end_dt):
    total = len(bid_list) + len(pre_list)
    period = f"{start_dt[:4]}-{start_dt[4:6]}-{start_dt[6:8]} ~ {end_dt[:4]}-{end_dt[4:6]}-{end_dt[6:8]}"

    return f"""<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8">{STYLE}</head>
<body><div class="wrap">
  <div class="header">
    <h2>📋 나라장터 RFP 현황</h2>
    <p>{today_str} &nbsp;|&nbsp; 총 <strong>{total}건</strong> &nbsp;|&nbsp; 조회기간: {period}</p>
  </div>
  <div class="body">

    <div class="section-title">① 입찰공고 <span class="badge">{len(bid_list)}건</span></div>
    <table>
      <thead><tr>
        <th style="width:35%">공고명</th>
        <th style="width:22%">발주처</th>
        <th style="width:13%">금액</th>
        <th style="width:17%">마감일</th>
        <th style="width:13%">공고번호</th>
      </tr></thead>
      <tbody>{bid_rows(bid_list)}</tbody>
    </table>

    <div class="section-title">② 사전규격공고 <span class="badge">{len(pre_list)}건</span></div>
    <table>
      <thead><tr>
        <th style="width:40%">공고명</th>
        <th style="width:25%">발주처</th>
        <th style="width:15%">금액</th>
        <th style="width:20%">의견마감일</th>
      </tr></thead>
      <tbody>{pre_rows(pre_list)}</tbody>
    </table>

  </div>
  <div class="footer">
    검색 키워드: ISP · ISMP · 정보화전략 &nbsp;|&nbsp; 세부품명번호: {PRDLST_CD} &nbsp;|&nbsp;
    자동발송 (GitHub Actions)
  </div>
</div></body></html>"""

def make_empty_html(today_str):
    return f"""<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8">{STYLE}</head>
<body><div class="wrap">
  <div class="header">
    <h2>📋 나라장터 RFP 현황</h2>
    <p>{today_str}</p>
  </div>
  <div class="body">
    <p class="empty" style="padding:20px 0">해당 기간 내 신규 공고가 없습니다.</p>
  </div>
  <div class="footer">
    검색 키워드: ISP · ISMP · 정보화전략 &nbsp;|&nbsp; 세부품명번호: {PRDLST_CD}
  </div>
</div></body></html>"""


# ─────────────────────────────────────────────────────────
# 4. 이메일 발송
# ─────────────────────────────────────────────────────────
def send_email(subject, html_body):
    recipients = [r.strip() for r in MAIL_TO.split(',')]
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From']    = MAIL_FROM
    msg['To']      = ', '.join(recipients)
    msg.attach(MIMEText(html_body, 'html', 'utf-8'))

    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as srv:
        srv.ehlo()
        srv.login(SMTP_USER, SMTP_PASS)
        srv.sendmail(MAIL_FROM, recipients, msg.as_string())

    print(f'✅ 이메일 발송 완료 → {recipients}')


# ─────────────────────────────────────────────────────────
# 5. 메인
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
