import os
import sys

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='短视频文案生成工具')
    parser.add_argument('--gui', action='store_true', help='启动图形化界面')
    parser.add_argument('--cli', action='store_true', help='使用命令行模式')
    args = parser.parse_args()
    
    if args.gui:
        run_gui()
    else:
        run_cli()


def run_gui():
    from gui import main as gui_main
    gui_main()


def run_cli():
    import config
    from audio_extractor import AudioExtractor
    from speech_to_text import SpeechToText
    from ai_rewriter import AIRewriter

    def get_input(prompt, default=None):
        if default:
            value = input(f"{prompt} [{default}]: ").strip()
            return value if value else default
        else:
            return input(f"{prompt}: ").strip()

    print("\n" + "=" * 50)
    print("  短视频文案生成工具")
    print("=" * 50)
    print()
    
    video_path = get_input("视频文件路径")
    if not video_path or not os.path.exists(video_path):
        print("[错误] 视频文件不存在")
        return
    
    keywords_input = get_input("产品关键词(多个用逗号分隔)", "护肤,抗衰")
    keywords = [k.strip() for k in keywords_input.split(",") if k.strip()]
    
    model_size_input = get_input("Whisper模型大小(base/small/medium)", "base")
    model_size = model_size_input.strip() or "base"
    
    try:
        print("\n[步骤1] 提取音频...")
        extractor = AudioExtractor()
        audio_path = extractor.extract_audio(video_path)
        
        print("\n[步骤2] 语音识别...")
        stt = SpeechToText(model_size=model_size)
        transcript_text = stt.transcribe(audio_path)
        stt.save_transcript(transcript_text)
        
        print("\n[步骤3] AI改写...")
        rewriter = AIRewriter()
        rewritten_text = rewriter.rewrite(transcript_text, keywords)
        
        print("\n[步骤4] 保存结果...")
        keywords_str = ", ".join(keywords)
        output_content = f"""{'=' * 50}
视频文案生成结果
{'=' * 50}

产品关键词: {keywords_str}

{'=' * 50}
原始转写文本:
{'=' * 50}
{transcript_text}

{'=' * 50}
AI改写文案:
{'=' * 50}
{rewritten_text}

{'=' * 50}
"""
        
        output_path = os.path.join(config.OUTPUT_DIR, config.OUTPUT_FILENAME)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(output_content)
        
        print("\n" + "=" * 50)
        print("文案生成完成!")
        print("=" * 50)
        print("\n【AI改写文案】:")
        print(rewritten_text)
        print(f"\n结果已保存到: {output_path}")
        
    except KeyboardInterrupt:
        print("\n\n[INFO] 用户取消操作")
    except Exception as e:
        print(f"\n[错误] 执行失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
