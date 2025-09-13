import os
import azure.cognitiveservices.speech as speechsdk
import threading
from django.conf import settings

def speech_to_text(): #
    """
    연속 음성 인식:
      - 백그라운드에서 사용자가 말하는 모든 구간을 인식해 누적
      - 사용자가 엔터(빈 입력)로 중단할 때까지 대기
    Returns:
      누적된 텍스트 문자열, 인식된 부분이 없으면 None
    """
    subscription = getattr(settings, 'AZURE_SPEECH_KEY', None)
    endpoint     = getattr(settings, 'AZURE_SPEECH_ENDPOINT', None)
    if not subscription or not endpoint:
        print("❌ 음성 서비스 키/엔드포인트 미설정")
        return None

    speech_config = speechsdk.SpeechConfig(
        subscription=subscription,
        endpoint=endpoint
    )
    speech_config.speech_recognition_language = "ko-KR"

    audio_config = speechsdk.audio.AudioConfig(use_default_microphone=True)
    recognizer   = speechsdk.SpeechRecognizer(
        speech_config=speech_config,
        audio_config=audio_config
    )

    lock = threading.Lock()
    segments = []

    def on_recognized(evt):
        if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech:
            text = evt.result.text.strip()
            print(f"✅ 인식: {text}")
            with lock:
                segments.append(text)

    def on_canceled(evt):
        print(f"❌ 인식 취소: {evt.result.cancellation_details.reason}")

    recognizer.recognized.connect(on_recognized)
    recognizer.canceled.connect(on_canceled)

    # 연속 인식 시작
    recognizer.start_continuous_recognition()
    print("🎤 음성 인식을 시작했습니다. 답변을 마치셨으면 엔터를 치세요.")

    # 사용자 엔터 입력 대기
    input()

    # 인식 중지
    recognizer.stop_continuous_recognition()

    # 누적된 텍스트 합치기
    with lock:
        full_answer = " ".join(segments).strip()

    if full_answer:
        print(f"🎉 최종 인식된 답변: {full_answer}")
        return full_answer
    else:
        print("❌ 음성을 인식하지 못했습니다.")
        return None

def text_to_speech(text, voice_name="ko-KR-SunHiNeural"):
    """텍스트를 음성으로 변환하여 스피커로 출력"""
    try:
        speech_config = speechsdk.SpeechConfig(
            subscription=getattr(settings, 'AZURE_SPEECH_KEY', None),
            endpoint=getattr(settings, 'AZURE_SPEECH_ENDPOINT', None)
        )
        
        # 한국어 음성 설정 (여성: SunHiNeural, 남성: HyunsuNeural)
        speech_config.speech_synthesis_voice_name = voice_name
        
        # 기본 스피커로 출력
        speech_synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config)
        
        print(f"🔊 음성 출력 중: {text[:50]}{'...' if len(text) > 50 else ''}")
        
        # 텍스트를 음성으로 변환하여 재생
        result = speech_synthesizer.speak_text_async(text).get()
        
        if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
            print("✅ 음성 출력 완료")
            return True
        elif result.reason == speechsdk.ResultReason.Canceled:
            cancellation_details = result.cancellation_details
            print(f"❌ 음성 합성이 취소되었습니다: {cancellation_details.reason}")
            
            if cancellation_details.reason == speechsdk.CancellationReason.Error:
                print(f"오류 코드: {cancellation_details.error_code}")
                print(f"오류 세부사항: {cancellation_details.error_details}")
            
            return False
        else:
            print(f"❌ 알 수 없는 결과: {result.reason}")
            return False
            
    except Exception as e:
        print(f"❌ 음성 출력 중 오류 발생: {str(e)}")
        return False

def text_to_speech_with_ssml(text, voice_name="ko-KR-SunHiNeural", rate="medium", pitch="medium"):
    """SSML을 사용한 고급 음성 합성 (속도, 톤 조절 가능)"""
    try:
        speech_config = speechsdk.SpeechConfig(
            subscription=getattr(settings, 'AZURE_SPEECH_KEY', None),
            endpoint=getattr(settings, 'AZURE_SPEECH_ENDPOINT', None)
        )
        
        speech_synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config)
        
        # SSML 형식으로 음성 스타일 조정
        ssml_text = f'''
        <speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="ko-KR">
            <voice name="{voice_name}">
                <prosody rate="{rate}" pitch="{pitch}">
                    {text}
                </prosody>
            </voice>
        </speak>
        '''
        
        print(f"🔊 SSML 음성 출력 중: {text[:50]}{'...' if len(text) > 50 else ''}")
        
        result = speech_synthesizer.speak_ssml_async(ssml_text).get()
        
        if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
            print("✅ SSML 음성 출력 완료")
            return True
        else:
            print(f"❌ SSML 음성 출력 실패: {result.reason}")
            return False
            
    except Exception as e:
        print(f"❌ SSML 음성 출력 중 오류 발생: {str(e)}")
        return False
