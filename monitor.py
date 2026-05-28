#!/usr/bin/env python3
"""
나라장터 RFP 자동 모니터링
──────────────────────────
- 평일 아침 7시 (KST) GitHub Actions 실행
- 입찰공고 + 사전규격공고 수집
- Claude API로 HTML 요약 생성
- 회사 SMTP로 이메일 발송
"""

import os
import json
import smtplib
import requests
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

# ── 환경변수 (GitHub Secrets) ─────────────────────────────
DATA_API_KEY  = os.environ['DATA_GO_API_KEY']    # 공공데이터포털 인증키
ANTHROPIC_KEY = os.environ['ANTHROPIC_API_KEY']  # Claude API 키
SMTP_HOST     = os.environ['SMTP_HOST']           # 예: mail.yourcompany.com
SMTP_PORT     = int(os.environ.get('SMTP_PORT', '587'))
SMTP_USER     = os.environ['SMTP_USER']           # 발신 계정
SMTP_PASS     = os.environ['SMTP_PASSWORD']       # 발신 비밀번호
MAIL_FROM     = os.environ['MAIL_FROM']           # 발신자 주소
MAIL_TO       = os.environ['MAIL_TO']             # 수신자 (쉼표 구분 가능)

# ── 검색 조건 ─────────────────────────────────────────────
KEYWORDS  = ['ISP', 'ISMP', '정보화전략']
PRDLST_CD = '8010150701'   # 세부품명번호: 정보전략계획수립(ISP)

# ── API 엔드포인트 ────────────────────────────────────────
BID_URL = 'https://apis.data.go.kr/1230000/BidPublicInfoService04/getBidPblancListInfoServc'
PRE_URL = 'https://apis.data.go.kr/1230000/PrePrddlInfoService/getPrePrddlInfoListServc'

KST = ZoneInfo('Asia/Seoul')


# ─────────────────────────────────────────────────────────
# 1. 날짜 범위
# ─────────────────────────────────────────────────────────
def get_date_range():
    """오늘 기준 조회 범위 반환 (월요일이면 금-월 포함)"""
    today = datetime.now(KST)
    days_back = 3 if today.weekday() == 0 else 1   # 월요일 → 금요일부터
    start = today - timedelta(days=days_back)
    return (
        start.strftime('%Y%m%d') + '0000',
        today.strftime('%Y%m%d') + '2359',
    )


# ─────────────────────────────────────────────────────────
# 2. API 호출 공통 함수
# ─────────────────────────────────────────────────────────
def _call(url, params, bucket, seen, id_key):
    try:
        r = requests.get(url, params=params, timeout=30)
        r.raise_for_status()
        body = r.json().get('response', {}).get('body', {})
        items = body.get('items', [])
        if isinstance(items, dict):
            items = [items]
        for item in (items or []):
            uid = item.get(id_key, '')
            if uid and uid not in seen:
                seen.add(uid)
                bucket.append(item)
    except Exception as e:
        print(f'  ⚠ API 오류 ({url}): {e}')


# ─────────────────────────────────────────────────────────
# 3. 입찰공고
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
        _call(BID_URL, {**base, 'bidNtceNm': kw}, results, seen, 'bidNtceNo')
    # 세부품명번호로 추가 조회
    _call(BID_URL, {**base, 'dtilPrdlstCd': PRDLST_CD}, results, seen, 'bidNtceNo')
    return results


# ─────────────────────────────────────────────────────────
# 4. 사전규격공고
# ─────────────────────────────────────────────────────────
def fetch_pre_notices(start_dt, end_dt):
    results, seen = [], set()
    base = {
        'serviceKey': DATA_API_KEY,
        'numOfRows': '100', 'pageNo': '1',
        'inqryBgnDt': start_dt[:8],   # yyyyMMdd
        'inqryEndDt': end_dt[:8],
        'type': 'json',
    }
    for kw in KEYWORDS:
        _call(PRE_URL, {**base, 'prdctClsfcNoNm': kw}, results, seen, 'prePrddlNo')
    _call(PRE_URL, {**base, 'dtilPrdlstCd': PRDLST_CD}, results, seen, 'prePrddlNo')
    return results


# ─────────────────────────────────────────────────────────
# 5. Claude API로 HTML 이메일 생성
# ─────────────────────────────────────────────────────────
def make_html_with_claude(bid_list, pre_list, today_str):
    bid_json = json.dumps(bid_list, ensure_ascii=False, indent=2) if bid_list else '없음'
    pre_json = json.dumps(pre_list, ensure_ascii=False, indent=2) if pre_list else '없음'

    prompt = f"""아래 데이터를 바탕으로 업무용 HTML 이메일 본문을 작성해주세요.
날짜: {today_str}

[입찰공고 원시 데이터]
{bid_json}

[사전규격공고 원시 데이터]
{pre_json}

작성 규칙:
- <div>로 시작하는 HTML fragment만 출력 (DOCTYPE/html/body 태그 없이)
- 인라인 CSS 사용, 폰트: 맑은고딕, sans-serif
- 섹션 구분: ① 입찰공고, ② 사전규격공고
- 각 공고: 공고명 / 발주처 / 사업금액 / 마감일 / 공고번호 표시
- 공고명은 가능하면 나라장터 링크로 연결 (bidNtceUrl 필드 활용)
- 공고 없는 섹션은 "해당 기간 공고 없음" 회색 텍스트로 표시
- 상단 인사말: "안녕하세요, {today_str} 나라장터 RFP 현황입니다."
- 하단 푸터: 검색조건(키워드·세부품명번호) 회색 소문자로 표시
- 전체적으로 깔끔한 테이블 레이아웃, 헤더 배경 #1a4b8c 흰색 글씨"""

    resp = requests.post(
        'https://api.anthropic.com/v1/messages',
        headers={
            'x-api-key': ANTHROPIC_KEY,
            'anthropic-version': '2023-06-01',
            'content-type': 'application/json',
        },
        json={
            'model': 'claude-sonnet-4-20250514',
            'max_tokens': 4096,
            'messages': [{'role': 'user', 'content': prompt}],
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()['content'][0]['text']


def make_empty_html(today_str):
    return f"""<div style="font-family:'맑은고딕',sans-serif;max-width:700px;padding:24px;">
  <p>안녕하세요, {today_str} 나라장터 RFP 현황입니다.</p>
  <p style="color:#555;">해당 기간 내 신규 공고가 없습니다.</p>
  <hr style="border:none;border-top:1px solid #eee;margin:20px 0;">
  <p style="color:#aaa;font-size:11px;">
    검색 키워드: ISP, ISMP, 정보화전략 &nbsp;|&nbsp; 세부품명번호: {PRDLST_CD}
  </p>
</div>"""


# ─────────────────────────────────────────────────────────
# 6. 이메일 발송
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
# 7. 메인
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
        html_body = make_html_with_claude(bid_list, pre_list, today_str)

    send_email(subject, html_body)


if __name__ == '__main__':
    main()
