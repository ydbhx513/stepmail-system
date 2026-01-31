"""
LLM常駐サービス (LRS) サーバー
TCPソケット経由でメール生成リクエストを処理します。
"""

import socket
import json
from llama_cpp import Llama

# サーバー設定
HOST = "localhost"
PORT = 5000
MODEL_PATH = "model.gguf"

# LLMインスタンス(グローバル変数として保持)
llm = None


def load_model():
    """
    起動時にLLMモデルをロードします。
    """
    global llm
    print(f"[LRS] モデル '{MODEL_PATH}' をロード中...")
    
    llm = Llama(
        model_path=MODEL_PATH,
        n_ctx=2048,  # コンテキストウィンドウ
        n_threads=4,  # CPUスレッド数
        n_gpu_layers=0  # CPU推論の場合は0
    )
    
    print("[LRS] モデルのロードが完了しました。")


def get_tone_instruction(tone):
    """
    トーンに応じた文体指示を返す
    """
    tone_instructions = {
        "丁寧かつ熱意をもって": "非常に丁寧で敬意ある表現を使い、熱意と誠意が伝わる文章にしてください。",
        "フォーマルで厳格に": "ビジネスフォーマルな表現を徹底し、格式高く厳格な文体で書いてください。",
        "フレンドリーで親しみやすく": "親しみやすく柔らかい表現を使い、友人に話すようなカジュアルで温かみのある文章にしてください。",
        "簡潔で要点を押さえて": "無駄を省き、要点のみを簡潔明瞭に伝える文章にしてください。",
        "説得力をもって積極的に": "説得力のある表現を使い、積極的で前向きな印象を与える文章にしてください。",
        "控えめで謙虚に": "控えめで謙虚な表現を心がけ、押し付けがましくない柔らかい文章にしてください。",
        "情熱的でエネルギッシュに": "情熱とエネルギーが溢れる表現を使い、読み手を鼓舞するような活気ある文章にしてください。"
    }
    
    return tone_instructions.get(tone, "適切なトーンで書いてください。")


def generate_prompt(step_count, topic, key_points, tone, customer_name="お客様", related_products=None):
    tone_instruction = get_tone_instruction(tone)
    
    # ステップごとの内容定義
    if step_count == 1:
        purpose = "購入のお礼と、届くまでのワクワク感を伝える"
    elif step_count == 2:
        purpose = f"{topic}の効果的な使い方を提案し、利用を促す"
    elif step_count == 3:
        purpose = f"{topic}と相性の良い関連商品を提案する"
        if related_products:
            # 関連商品がある場合は具体的に指定
            purpose += f"\n関連商品: {', '.join(related_products)}"
    elif step_count == 4:
        purpose = "商品の感想（レビュー）を書いてもらうようお願いする"
    else:
        purpose = "レビューの再依頼（控えめに）"

    # プロンプト構成：シンプルかつ強力に型を指定
    prompt = f"""###
以下は、ECサイトの丁寧なステップメールの作成例です。

【例】
商品: 熟成黒にんにく
目的: サンクスメール
件名: ご購入ありがとうございます
本文:
{customer_name} 様

この度は「熟成黒にんにく」をご購入いただき、誠にありがとうございます。

到着まで今しばらくお待ちください。

今後ともよろしくお願いいたします。

###
以下の条件でメールを作成してください。解説は不要です。
また、メール本文として読みやすいように文章の段落ごとで改行してください。

【条件】
商品: {topic}
詳細: {key_points}
文体: {tone_instruction}
目的: {purpose}

件名:"""
    return prompt

def process_request(request_data):
    try:
        customer_name = request_data.get("customer_name", "お客様")
        step_count = request_data.get("step_count", 1)
        topic = request_data.get("topic", "")
        key_points = request_data.get("key_points", "")
        tone = request_data.get("tone", "丁寧")
        related_products = request_data.get("related_products", None)
        
        print(f"[LRS] リクエスト処理: {topic}, ステップ {step_count}")
        
        # プロンプト生成
        prompt = generate_prompt(step_count, topic, key_points, tone, customer_name, related_products)
        
        # LLM推論実行
        # generate_promptの最後が "件名:" で終わっているため、続きを書かせる
        response = llm(
            prompt,
            max_tokens=256,       # 十分な長さを確保
            temperature=0.1,
            top_p=0.9,
            repeat_penalty=1.2,   # 【重要】繰り返しを強力に防ぐ
            stop=["件名:", "---", "###", "宛名:"],
            echo=False
        )
        
        # 生成テキストの取得
        generated_text = response["choices"][0]["text"]
        
        # --- 【重要】ここから強力な後処理（クリーニング）を行います ---

        # 1. もしAIが「様」や「 様」から書き始めていたら、先頭のそれを削除
        #    (「山田太郎 様 様」になるのを防ぐ)
        if generated_text.strip().startswith("様"):
            generated_text = generated_text.strip().lstrip("様").strip()
        elif generated_text.strip().startswith(f"{customer_name} 様"):
             # 万が一、フルネームから書き直していたらそれも削除
            generated_text = generated_text.replace(f"{customer_name} 様", "", 1).strip()

        # 2. 本文中にまた「山田太郎 様」が出てきたら、それはループの始まりなので、そこより後ろを捨てる
        if f"{customer_name} 様" in generated_text:
            generated_text = generated_text.split(f"{customer_name} 様")[0]

        # 3. 「よろしくお願いいたします」で終わらせる（それ以降のゴミをカット）
        #    いろんなパターンに対応 ("よろしくお願い致します", "今後とも..."など)
        end_phrases = ["よろしくお願いいたします", "よろしくお願い致します", "お待ちしております"]
        for phrase in end_phrases:
            if phrase in generated_text:
                # そのフレーズまでで切って、フレーズ自体は残す
                parts = generated_text.split(phrase)
                generated_text = parts[0] + phrase + "。"
                break # 最初に見つかった締めくくりで終了

        # 4. 改行の確保（読みやすくする）
        #    句点「。」のあとに改行がなければ強制的に改行を入れる
        generated_text = generated_text.replace("。", "。\n\n")
        full_text = generated_text
        subject = ""
        body = ""

        # 改行で分割して解析
        lines = full_text.split('\n')
        
        # 1行目が件名である可能性が高い
        if lines:
            subject = lines[0].replace("件名:", "").strip()
        
        # "本文:" というキーワードを探す、なければ2行目以降を本文とする
        body_start_index = 1
        for i, line in enumerate(lines):
            if "本文:" in line:
                body_start_index = i + 1
                break
        
        if len(lines) > body_start_index:
            body = "\n".join(lines[body_start_index:]).strip()
        else:
            # 万が一改行がない場合や "本文:" がない場合のフォールバック
            # 最初の句点「。」や全角スペースで無理やり切る処理
            if "本文:" in generated_text:
                parts = generated_text.split("本文:")
                if not subject: subject = parts[0].strip()
                body = parts[1].strip()
            else:
                body = generated_text.replace(subject, "").strip()

        # 最終確認：連続する改行が多すぎたら2つに縮める
        import re
        body = re.sub(r'\n{3,}', '\n\n', body)

        # 宛名の補正（本文の先頭になければ追加）
        if customer_name not in body:
            body = f"{customer_name} 様\n\n{body}"

        # 件名の補正（空ならデフォルトを入れる）
        if not subject:
            subject = f"【{topic}】についてのご案内"

        print("[LRS] 生成完了")
        
        return {
            "status": "success",
            "subject": subject,
            "body": body
        }
        
    except Exception as e:
        print(f"[LRS] エラー: {e}")
        return {
            "status": "error",
            "message": str(e)
        }


def start_server():
    """
    TCPサーバーを起動し、リクエストを処理します。
    """
    # モデルをロード
    load_model()
    
    # ソケット作成
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind((HOST, PORT))
    server_socket.listen(5)
    
    print(f"[LRS] サーバー起動: {HOST}:{PORT}")
    print("[LRS] クライアントからの接続を待機中...")
    
    try:
        while True:
            client_socket, address = server_socket.accept()
            print(f"[LRS] クライアント接続: {address}")
            
            try:
                # リクエストデータを受信(最大4096バイト)
                data = client_socket.recv(4096).decode("utf-8")
                
                if not data:
                    continue
                
                # JSONパース
                request_data = json.loads(data)
                
                # リクエスト処理
                response_data = process_request(request_data)
                
                # レスポンス送信
                response_json = json.dumps(response_data, ensure_ascii=False)
                client_socket.sendall(response_json.encode("utf-8"))
                
            except Exception as e:
                print(f"[LRS] 処理エラー: {e}")
                error_response = json.dumps({
                    "status": "error",
                    "message": str(e)
                }, ensure_ascii=False)
                client_socket.sendall(error_response.encode("utf-8"))
            
            finally:
                client_socket.close()
    
    except KeyboardInterrupt:
        print("\n[LRS] サーバーを終了します...")
    
    finally:
        server_socket.close()


if __name__ == "__main__":
    start_server()