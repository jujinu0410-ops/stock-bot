function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('YouTube 매수감시')
    .addItem('1. Cloud Run API 설정', 'configureYouTubeSignalApi')
    .addItem('2. 5분/30분 트리거 설치', 'installYouTubeWatchTriggersV2')
    .addSeparator()
    .addItem('후보 지금 동기화', 'refreshYouTubeCandidatePoolV2')
    .addItem('신호 지금 검사', 'monitorYouTubeCandidatesV2')
    .addItem('API 연결 확인', 'checkSignalApiHealth')
    .addToUi();
}

function configureYouTubeSignalApi() {
  const ui = SpreadsheetApp.getUi();
  const urlPrompt = ui.prompt(
    'Cloud Run URL',
    '배포 완료 화면의 Service URL을 붙여넣으세요. 예: https://youtube-signal-service-xxxxx.a.run.app',
    ui.ButtonSet.OK_CANCEL
  );
  if (urlPrompt.getSelectedButton() !== ui.Button.OK) return;

  const tokenPrompt = ui.prompt(
    '공유 토큰',
    '배포 스크립트가 클립보드에 복사한 API token을 붙여넣으세요. 토큰은 시트 셀에는 저장되지 않습니다.',
    ui.ButtonSet.OK_CANCEL
  );
  if (tokenPrompt.getSelectedButton() !== ui.Button.OK) return;

  setSignalApiConfig(urlPrompt.getResponseText().trim(), tokenPrompt.getResponseText().trim());
  ui.alert('API 설정 완료', '이제 “5분/30분 트리거 설치”를 한 번 실행하면 됩니다.', ui.ButtonSet.OK);
}
