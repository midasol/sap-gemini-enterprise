# Cloud Run OAuth 콜백 서비스

SAP OAuth 리다이렉트 콜백을 수신하고 GCP Secret Manager를 통해 에이전트에 전달하는 독립형 Flask 마이크로서비스입니다.

## 이 서비스가 필요한 이유

사용자가 OAuth를 통해 SAP에 인증할 때, SAP은 인가 코드와 함께 콜백 URL로 리다이렉트합니다. Vertex AI Agent Engine에서 실행 중인 에이전트는 HTTP 콜백을 직접 수신할 수 없으므로, 이 Cloud Run 서비스가 중개 역할을 합니다:

1. SAP이 인가 코드와 함께 사용자의 브라우저를 이 서비스로 리다이렉트
2. 서비스가 코드를 GCP Secret Manager에 저장
3. 에이전트가 Secret Manager를 폴링하여 코드를 자동으로 수신

## 엔드포인트

| 엔드포인트 | 메서드 | 설명 |
|-----------|--------|------|
| `/callback` | GET | `code`와 `state` 쿼리 파라미터를 포함한 SAP OAuth 리다이렉트 수신 |
| `/identify` | POST | One Tap에서 Google ID 토큰을 수신하여 사용자 신원 연결 |
| `/health` | GET | 헬스 체크, `{"status": "ok"}` 반환 |

### `/callback` 흐름

1. SAP 리다이렉트에서 `code`, `state` (선택적으로 `error`) 수신
2. `sap-oauth-pending-{state[:16]}` 이름으로 Secret Manager 시크릿 생성
3. `{"code": "...", "state": "...", "timestamp": "..."}` 형태로 시크릿 버전 저장
4. Google One Tap 통합이 포함된 HTML 성공 페이지 반환

### `/identify` 흐름

1. `{"credential": "<google_id_token>", "secret_id": "..."}` POST 요청 수신
2. `https://oauth2.googleapis.com/tokeninfo`에 대해 Google ID 토큰 검증
3. audience가 설정된 `GOOGLE_CLIENT_ID`와 일치하는지 확인
4. `google_user_email`을 포함하도록 대기 중인 시크릿 업데이트
5. 에이전트가 이 이메일을 사용하여 OAuth 코드를 올바른 ADK 사용자와 매칭

## 설정

| 환경 변수 | 필수 | 설명 |
|-----------|------|------|
| `GOOGLE_CLOUD_PROJECT` | 예 | Secret Manager용 GCP 프로젝트 ID |
| `GOOGLE_CLIENT_ID` | 아니오 | One Tap 검증용 Google OAuth Client ID |
| `PORT` | 아니오 | 서버 포트 (기본값: `8080`) |

## 배포

### 빌드 및 배포

```bash
cd cloud-run-oauth-callback

# 컨테이너 빌드
gcloud builds submit --tag gcr.io/$PROJECT_ID/sap-oauth-callback

# Cloud Run에 배포
gcloud run deploy sap-oauth-callback \
  --image gcr.io/$PROJECT_ID/sap-oauth-callback \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars "GOOGLE_CLOUD_PROJECT=$PROJECT_ID"
```

### 필요한 IAM 권한

Cloud Run 서비스 계정에 다음 권한이 필요합니다:
- `roles/secretmanager.secretAccessor` — 기존 시크릿 읽기
- `roles/secretmanager.secretVersionAdder` — 시크릿 버전 추가
- `roles/secretmanager.admin` — 새 시크릿 생성 (대기 중인 코드용)

## Docker 설정

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "2", "main:app"]
```

의존성: `flask`, `gunicorn`, `google-cloud-secret-manager`, `requests`

## 보안

- `/callback` 엔드포인트는 인증 불필요 (SAP이 사용자의 브라우저를 리다이렉트)
- 모든 사용자 입력은 HTML 렌더링 전에 `markupsafe.escape`로 이스케이프 처리
- Google ID 토큰은 Google의 `tokeninfo` 엔드포인트에 대해 검증되며 audience 확인 수행
- 인가 코드는 Secret Manager에 저장 (저장 시 암호화)
- 시크릿 ID는 인젝션 방지를 위해 sanitize 처리 (`re.sub(r"[^a-zA-Z0-9_-]", "_", ...)`)

---

- [English Documentation](../CLOUD_RUN_OAUTH_CALLBACK.md)
