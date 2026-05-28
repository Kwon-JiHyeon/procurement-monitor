# 나라장터 RFP 자동 모니터링

평일 아침 7시(KST)에 나라장터 입찰공고·사전규격공고를 수집하고  
Claude가 정리한 HTML 보고서를 회사 이메일로 자동 발송합니다.

---

## 검색 조건

| 항목 | 값 |
|---|---|
| 키워드 | ISP, ISMP, 정보화전략 |
| 세부품명번호 | 8010150701 (정보전략계획수립) |
| 공고 유형 | 입찰공고 + 사전규격공고 |
| 실행 주기 | 평일(월~금) 07:00 KST |

---

## 사전 준비

### 1. 공공데이터포털 API 키 발급
1. [data.go.kr](https://www.data.go.kr) 회원가입
2. **나라장터 입찰공고정보서비스 v2** 활용 신청 → 인증키 발급
3. (사전규격공고) **사전규격공고정보서비스** 도 별도 신청 필요

> ⚠️ 발급 후 1~2일 활성화 대기 필요

### 2. Anthropic API 키
- [console.anthropic.com](https://console.anthropic.com) 에서 발급

### 3. 회사 SMTP 정보
- 메일 서버 호스트, 포트(보통 587), 계정/비밀번호 확인

---

## GitHub 셋업

### Step 1. 저장소 생성 & 코드 업로드
```bash
git init rfp-monitor
cd rfp-monitor
# 이 폴더의 파일들을 복사 후
git add .
git commit -m "init"
git remote add origin https://github.com/YOUR_NAME/rfp-monitor.git
git push -u origin main
```

### Step 2. Secrets 등록
GitHub 저장소 → **Settings > Secrets and variables > Actions > New repository secret**

| Secret 이름 | 값 예시 |
|---|---|
| `DATA_GO_API_KEY` | `abc123xyz...` (공공데이터포털 인증키) |
| `ANTHROPIC_API_KEY` | `sk-ant-...` |
| `SMTP_HOST` | `mail.yourcompany.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | `yourname@yourcompany.com` |
| `SMTP_PASSWORD` | `your_password` |
| `MAIL_FROM` | `yourname@yourcompany.com` |
| `MAIL_TO` | `a@co.com,b@co.com` (쉼표 구분) |

### Step 3. 수동 테스트 실행
GitHub 저장소 → **Actions > 나라장터 RFP 모니터링 > Run workflow**

---

## 파일 구조

```
rfp-monitor/
├── .github/
│   └── workflows/
│       └── daily_rfp.yml   # 스케줄러 (평일 KST 07:00)
├── monitor.py               # 메인 스크립트
├── requirements.txt
└── README.md
```

---

## 주의사항

- **SMTP SSL 방식**: 현재 STARTTLS(포트 587) 기준. 포트 465(SSL) 사용 시
  `monitor.py`의 `send_email()` 함수를 `smtplib.SMTP_SSL`로 변경
- **사전규격공고 API**: 공공데이터포털에서 파라미터명이 변경될 수 있음.
  오류 발생 시 [API 명세](https://www.data.go.kr/iim/api/selectAPIAcountView.do) 재확인
- **월요일**: 금~월 3일치 공고를 한 번에 수집 (주말 누락 방지)
- GitHub Actions 무료 플랜: 월 2,000분 → 1회 약 1~2분 기준 충분
