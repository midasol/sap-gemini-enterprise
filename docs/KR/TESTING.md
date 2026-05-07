# 테스트 가이드

## 테스트 실행

```bash
# 전체 테스트 실행
pytest

# 상세 출력으로 실행
pytest -v

# 특정 테스트 파일 실행
pytest tests/test_auth.py

# 특정 테스트 클래스 또는 메서드 실행
pytest tests/test_sap_oauth.py::TestSAPOAuthConfig
pytest tests/test_auth.py::TestSAPAuthenticator::test_generate_auth_url
```

### 사전 요구 사항

개발 의존성을 설치합니다:

```bash
pip install -e ".[dev]"
# 또는 uv 사용
uv sync --group dev
```

### 설정

테스트 설정은 `pyproject.toml`에 있습니다:

```toml
[tool.pytest.ini_options]
pythonpath = "."
asyncio_mode = "auto"                        # 비동기 테스트 자동 감지
asyncio_default_fixture_loop_scope = "function"
```

## 테스트 구조

```
tests/
  conftest.py              # 공유 픽스처 (clean_env, sap_oauth_env, sap_oauth_config)
  test_auth.py             # SAPUserToken, SAPAuthenticator, SAPAuthorizationCodeStrategy
  test_config.py           # SAPConnectionConfig 검증, 환경 변수 통합
  test_sap_oauth.py        # 전체 OAuth 흐름: 단위, 통합, E2E 테스트
  test_sap_auth_config.py  # ADK AuthConfig 빌더 (build_sap_auth_config)
  test_adk_auth_flow.py    # 에이전트 tool 함수에서의 ADK 인증 통합
  test_integration.py      # 모듈 간 통합 테스트
```

## 테스트 수준

### 단위 테스트 (네트워크 불필요)

mock을 사용하여 코드 로직을 격리 테스트합니다. `test_auth.py`, `test_config.py`, `test_sap_auth_config.py`의 모든 테스트가 단위 테스트입니다.

예시:
- 토큰 유효성/만료 검사
- 설정 검증 (유효/무효 입력)
- PKCE를 사용한 인증 URL 생성
- 헤더 구성

### 통합 테스트 (SAP mock 사용)

mock된 HTTP 응답을 사용하여 다중 컴포넌트 흐름을 테스트합니다. `test_sap_oauth.py`와 `test_integration.py`에 있습니다.

예시:
- mock SAP 토큰 엔드포인트를 사용한 전체 OAuth 코드 교환
- mock HTTP를 사용한 SAPClient 인증 흐름
- mock 응답을 사용한 토큰 갱신
- 오류 처리 (invalid_grant, invalid_client)

### E2E 테스트 (실제 SAP, 기본적으로 건너뜀)

실제 SAP 시스템에 대한 엔드투엔드 테스트입니다. `test_sap_oauth.py`에 위치하며, `SAP_E2E_TEST=1`이 설정되지 않으면 건너뜁니다:

```bash
SAP_E2E_TEST=1 pytest tests/test_sap_oauth.py -k "e2e" -v
```

## 주요 픽스처 (`conftest.py`)

| 픽스처 | 스코프 | 설명 |
|--------|--------|------|
| `clean_env` | autouse | 각 테스트 전에 모든 `SAP_*` 환경 변수 제거 |
| `sap_oauth_env` | function | SAP OAuth 테스트를 위한 환경 변수 설정 |
| `sap_oauth_config` | function | 테스트용 `SAPConnectionConfig` 인스턴스 생성 |

## 테스트 커버리지

| 영역 | 파일 | 커버리지 |
|------|------|----------|
| 토큰 라이프사이클 | `test_auth.py` | 유효/만료/빈 토큰, 쿠키 |
| 설정 검증 | `test_config.py` | 필수 필드, 기본값, 환경 변수, 잘못된 입력 |
| OAuth PKCE 흐름 | `test_sap_oauth.py` | URL 생성, 코드 교환, 토큰 갱신, 오류 처리 |
| ADK AuthConfig | `test_sap_auth_config.py` | 환경 변수 유/무 시 빌더 |
| ADK 통합 | `test_adk_auth_flow.py` | ADK 자격 증명 유/무 시 tool 함수 |
| 모듈 간 | `test_integration.py` | Config→Auth→Strategy 체인, 예외 계층 구조 |

## 새 테스트 작성

1. 설정 객체가 필요한 테스트에는 `sap_oauth_config` 픽스처를 사용합니다
2. 환경 변수 테스트에는 `monkeypatch.setenv()`를 사용합니다 (`clean_env`에 의해 자동 정리)
3. 비동기 테스트에는 `@pytest.mark.asyncio`를 표시합니다 (`asyncio_mode = "auto"`를 통해 자동 감지)
4. HTTP 호출은 `unittest.mock.AsyncMock`과 `aiohttp` 응답 mock으로 처리합니다
5. 여러 파일에서 공유되는 픽스처는 `conftest.py`에 배치합니다

---

- [English Documentation](../TESTING.md)
