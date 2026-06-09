#!/usr/bin/env python3
"""
나라장터 RFP 자동 모니터링
"""

import os
import smtplib
import requests
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

DATA_API_KEY = os.environ['DATA_GO_API_KEY']
SMTP_HOST    = os.environ['SMTP_HOST']
SMTP_PORT    = int(os.environ.get('SMTP_PORT', '587'))
SMTP_USER    = os.environ['SMTP_USER']
SMTP_PASS    = os.environ['SMTP_PASSWORD']
MAIL_FROM    = os.environ['MAIL_FROM']
MAIL_TO      = os.environ['MAIL_TO']

KEYWORDS  = ['ISP', 'ISMP', '정보화전략']
PRDLST_CD = '8010150701'

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
        if n >= 1e8: return f'{n/1e8:.1f}억원 (VAT 포함)'
        if n >= 1e4: return f'{n/1e4:.0f}만원 (VAT 포함)'
        return f'{int(n):,}원 (VAT 포함)'
    except: return val or '-'

def fmt_date(val):
    s = str(val or '').replace('-','').replace(':','').replace(' ','')
    if len(s) >= 12: return f"{s[:4]}-{s[4:6]}-{s[6:8]} {s[8:10]}:{s[10:12]}"
    if len(s) >= 8:  return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return val or '-'

def make_html(bid_list, pre_list, today_str):
    font = "font-family:'맑은 고딕','Malgun Gothic',Arial,sans-serif;"
    
    # 입찰공고 섹션
    bid_section = ''
    for i, it in enumerate(bid_list, 1):
        name     = it.get('bidNtceNm', '-')
        org      = it.get('ntceInsttNm', '-')
        money    = fmt_money(it.get('presmptPrce','') or it.get('asignBdgtAmt',''))
        deadline = fmt_date(it.get('bidClseDt','') or it.get('opengDt',''))
        no       = it.get('bidNtceNo', '-')
        link     = f"https://www.g2b.go.kr:8101/ep/invitation/publish/bidInfoDtl.do?bidno={no}&bidseq=000"

        bid_section += f"""
<p style="{font}margin:0 0 4px 0">
  <span style="font-size:14px;font-weight:bold">{i}. {name}</span>
</p>
<p style="{font}font-size:13px;margin:0 0 2px 0;color:#222">발주처 : {org}</p>
<p style="{font}font-size:13px;margin:0 0 2px 0;color:#222">금액 : {money}</p>
<p style="{font}font-size:13px;margin:0 0 2px 0;color:#222">제안서 마감일 : {deadline}</p>
<p style="{font}font-size:13px;margin:0 0 16px 0;color:#222">공고 링크 : <a href="{link}" style="color:#1B3F7A">{link}</a></p>
"""

    if not bid_list:
        bid_section = f'<p style="{font}font-size:13px;color:#888;margin:0 0 16px 0">해당 기간 입찰공고 없음</p>'

    # 사전규격 섹션
    pre_section = ''
    for i, it in enumerate(pre_list, 1):
        name     = it.get('prdctClsfcNoNm', '-')
        org      = it.get('orderInsttNm', '-')
        money    = fmt_money(it.get('asignBdgtAmt',''))
        deadline = fmt_date(it.get('opninRgstClseDt',''))

        pre_section += f"""
<p style="{font}margin:0 0 4px 0">
  <span style="font-size:14px;font-weight:bold">{i}. {name}</span>
</p>
<p style="{font}font-size:13px;margin:0 0 2px 0;color:#222">발주처 : {org}</p>
<p style="{font}font-size:13px;margin:0 0 2px 0;color:#222">금액 : {money}</p>
<p style="{font}font-size:13px;margin:0 0 16px 0;color:#222">의견 마감일 : {deadline}</p>
"""

    if not pre_list:
        pre_section = f'<p style="{font}font-size:13px;color:#888;margin:0 0 16px 0">해당 기간 사전규격공고 없음</p>'

    divider = '<hr style="border:none;border-top:1px solid #ddd;margin:16px 0">'

    return f"""<!DOCTYPE html><html lang="ko">
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:20px;background:#fff">
<div style="max-width:700px">

  <p style="{font}font-size:13px;color:#444;margin:0 0 20px 0">
    안녕하세요 이사님, {today_str} 나라장터 현황입니다.
  </p>

  <p style="{font}font-size:15px;font-weight:bold;margin:0 0 12px 0">※ 입찰공고</p>
  {bid_section}

  {divider}

  <p style="{font}font-size:15px;font-weight:bold;margin:0 0 12px 0">※ 사전규격</p>
  {pre_section}

  {divider}

  <p style="{font}font-size:11px;color:#999;margin:0">
    검색 키워드: ISP · ISMP · 정보화전략 &nbsp;|&nbsp; 세부품명번호: {PRDLST_CD} &nbsp;|&nbsp; 자동발송 (GitHub Actions)
  </p>

</div>
</body></html>"""

def make_empty_html(today_str):
    font = "font-family:'맑은 고딕','Malgun Gothic',Arial,sans-serif;"
    return f"""<!DOCTYPE html><html lang="ko">
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:20px;background:#fff">
<div style="max-width:700px">
  <p style="{font}font-size:13px;color:#444;margin:0 0 20px 0">
    안녕하세요 이사님, {today_str} 나라장터 현황입니다.
  </p>
  <p style="{font}font-size:13px;color:#888;margin:0 0 20px 0">
    해당 기간 내 신규 공고가 없습니다.
  </p>
  <hr style="border:none;border-top:1px solid #ddd;margin:16px 0">
  <p style="{font}font-size:11px;color:#999;margin:0">
    검색 키워드: ISP · ISMP · 정보화전략 &nbsp;|&nbsp; 세부품명번호: {PRDLST_CD}
  </p>
</div>
</body></html>"""

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
        subject   = f'[나라장터] {today_str} 현황 - 총 {total}건'
        html_body = make_html(bid_list, pre_list, today_str)
    send_email(subject, html_body)

if __name__ == '__main__':
    main()
