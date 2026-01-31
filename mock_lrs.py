# =====================================================
# debug_tools/mock_lrs.py
# モックLRSサーバー(LLMなしで固定レスポンスを返す)
# =====================================================
"""
モックLRSサーバー
実際のLLMを使わず、固定レスポンスを返すテスト用サーバー
起動: python debug_tools/mock_lrs.py
"""
import socket
import json
import sys

HOST = 'localhost'
PORT = 5000


def get_tone_prefix(tone):
    """トーンに応じた文章の接頭辞や文体調整を返す"""
    tone_styles = {
        "丁寧かつ熱意をもって": {
            "prefix": "心より",
            "style": "です・ます調(丁寧)",
            "ending": "何卒よろしくお願い申し上げます。"
        },
        "フォーマルで厳格に": {
            "prefix": "謹んで",
            "style": "である調(格式)",
            "ending": "以上、ご確認のほどお願いいたします。"
        },
        "フレンドリーで親しみやすく": {
            "prefix": "いつも",
            "style": "です・ます調(カジュアル)",
            "ending": "気軽に連絡してくださいね!"
        },
        "簡潔で要点を押さえて": {
            "prefix": "",
            "style": "箇条書き中心",
            "ending": "よろしくお願いします。"
        },
        "説得力をもって積極的に": {
            "prefix": "ぜひ",
            "style": "積極的提案",
            "ending": "この機会をお見逃しなく!"
        },
        "控えめで謙虚に": {
            "prefix": "もしよろしければ",
            "style": "控えめ",
            "ending": "ご検討いただければ幸いです。"
        },
        "情熱的でエネルギッシュに": {
            "prefix": "ぜひとも",
            "style": "感嘆符多用",
            "ending": "一緒に最高の体験をしましょう!"
        }
    }
    
    return tone_styles.get(tone, {
        "prefix": "",
        "style": "通常",
        "ending": "よろしくお願いいたします。"
    })


def generate_mock_response(request):
    """ステップ数に応じたモックレスポンスを生成(EC向けステップメール)"""
    email = request.get('email', 'unknown@test.com')
    step_count = request.get('step_count', 1)
    topic = request.get('topic', '商品')
    key_points = request.get('key_points', '')
    tone = request.get('tone', '丁寧')
    customer_name = request.get('customer_name', 'お客様')
    related_products = request.get('related_products', None)
    
    # トーンスタイルを取得
    tone_style = get_tone_prefix(tone)
    prefix = tone_style["prefix"]
    ending = tone_style["ending"]
    
    # ステップごとにレスポンスを変える(EC向け)
    if step_count == 1:
        # ①購入当日 → サンクスメール
        if tone == "フォーマルで厳格に":
            subject = f"【ご注文承りました】{topic}のご購入について"
            body = f"""{customer_name}

この度は、{topic}をご注文いただき、誠にありがとうございます。

ご注文内容を確認いたしました。商品は現在発送準備中であります。

【商品情報】
{key_points}

商品到着後、速やかにご確認くださいますようお願い申し上げます。

{ending}"""
        
        elif tone == "フレンドリーで親しみやすく":
            subject = f"【ありがとうございます!】{topic}をお届けします♪"
            body = f"""{customer_name}

{topic}を選んでくれて、本当にありがとうございます!

注文を受け付けました。今、一生懸命準備しているので、楽しみに待っていてくださいね。

【商品情報】
{key_points}

届いたら、すぐに使ってみてください。きっと気に入ってもらえると思います!

{ending}"""
        
        elif tone == "簡潔で要点を押さえて":
            subject = f"【注文確認】{topic}"
            body = f"""{customer_name}

ご注文ありがとうございます。

■ 商品: {topic}
■ 状況: 発送準備中
■ 詳細: {key_points}

到着後、ご確認ください。

{ending}"""
        
        elif tone == "説得力をもって積極的に":
            subject = f"【素晴らしい選択です!】{topic}で新しい体験を"
            body = f"""{customer_name}

{prefix}{topic}をお選びいただき、ありがとうございます!

最高の決断をされましたね。この商品は多くのお客様から高い評価をいただいています。

【商品情報】
{key_points}

商品が届いたら、すぐに効果を実感していただけるはずです。

{ending}"""
        
        elif tone == "情熱的でエネルギッシュに":
            subject = f"【最高です!】{topic}があなたのもとへ!"
            body = f"""{customer_name}

わあ! {topic}を選んでくれてありがとうございます!!

すごくワクワクしています! この商品、本当に素晴らしいんです!

【商品情報】
{key_points}

届いたら、すぐに開けて試してみてください! きっと驚きますよ!!

{ending}"""
        
        else:  # デフォルト(丁寧かつ熱意をもって or 控えめで謙虚に)
            subject = f"【{prefix}ご購入ありがとうございます】{topic}をお届けいたします"
            body = f"""{customer_name}

この度は、{topic}をご購入いただき誠にありがとうございます。

ご注文いただいた商品は、現在準備中でございます。
商品がお手元に届きましたら、ぜひお試しください。

【商品情報】
{key_points}

ご不明な点やご質問がございましたら、お気軽にお問い合わせください。

{ending}"""
    
    elif step_count == 2:
        # ②2日後 → 使い方提案メール
        if tone == "フォーマルで厳格に":
            subject = f"【製品活用ガイド】{topic}の効果的な使用方法"
            body = f"""{customer_name}

平素よりご愛顧賜り、厚く御礼申し上げます。

{topic}ご購入より2日が経過いたしました。
製品は既にお手元に届いているものと存じます。

【効果的な使用方法】

{key_points}

推奨される使用法は以下の通りであります:

1. 定時使用による習慣化
2. 週次の集中的活用
3. 複数名での共同利用

{ending}"""
        
        elif tone == "フレンドリーで親しみやすく":
            subject = f"【もっと楽しく使おう!】{topic}活用術"
            body = f"""{customer_name}

{prefix}ありがとうございます!

{topic}を買ってから2日経ちましたね。
もう届きましたか? 使ってみましたか?

【おすすめの使い方】

{key_points}

こんな感じで使うと、もっと楽しめますよ:

1. 毎朝のルーティンに
2. 週末のスペシャルケアに
3. 家族や友達と一緒に

{ending}"""
        
        elif tone == "簡潔で要点を押さえて":
            subject = f"【活用Tips】{topic}"
            body = f"""{customer_name}

ご購入から2日経過しました。

■ 効果的な使い方:
{key_points}

■ おすすめ:
1. 朝の習慣化
2. 週末の集中ケア
3. 共同利用

{ending}"""
        
        else:  # その他のトーン
            subject = f"【{prefix}使い方のご提案】{topic}をもっと活用いただくために"
            body = f"""{customer_name}

いつもご利用いただきありがとうございます。

{topic}をご購入いただいてから2日が経過いたしました。
商品はお手元に届きましたでしょうか?

【効果的な使い方のご提案】

{key_points}

以下のような使い方がおすすめです:

1. 朝の習慣として取り入れる
2. 週末の特別なケアとして活用
3. ご家族やご友人と一緒にお楽しみいただく

{ending}"""
    
    elif step_count == 3:
        # ③7日後 → 関連提案メール
        # 関連商品のフォーマット
        related_text = ""
        if related_products and len(related_products) > 0:
            for idx, product in enumerate(related_products, 1):
                related_text += f"◆ {product}\n"
        else:
            # デフォルトの関連商品
            related_text = "◆ 関連商品A\n◆ 関連商品B\n◆ 関連商品C\n"
        
        if tone == "説得力をもって積極的に":
            subject = f"【見逃せない!】{topic}の効果を最大化する方法"
            body = f"""{customer_name}

{prefix}おすすめ情報があります!

{topic}をご購入から1週間。そろそろ効果を実感されている頃ではないでしょうか?

【さらに効果アップの秘訣】

{key_points}

多くのお客様が、これらの商品と組み合わせて驚きの結果を出しています:

{related_text}
{ending}"""
        
        elif tone == "控えめで謙虚に":
            subject = f"【ご参考まで】{topic}との組み合わせ提案"
            body = f"""{customer_name}

{prefix}ご提案させていただきます。

{topic}をご購入から1週間が経過いたしました。

【関連商品のご案内】

{key_points}

他のお客様から、以下の商品との組み合わせが好評とのお声をいただいております:

{related_text}
{ending}"""
        
        else:  # その他のトーン
            subject = f"【{prefix}あなたにおすすめ】{topic}と相性抜群の商品をご紹介"
            body = f"""{customer_name}

いつもご利用いただきありがとうございます。

{topic}をご購入から1週間が経過いたしました。
商品はご満足いただけておりますでしょうか?

【{topic}との組み合わせで効果アップ】

{key_points}

多くのお客様から、以下の商品との組み合わせで
さらに効果を実感されたとのお声をいただいております:

{related_text}
{ending}"""
    
    elif step_count == 4:
        # ④14日後 → レビュー依頼
        if tone == "フレンドリーで親しみやすく":
            subject = f"【ちょっと聞かせて!】{topic}どうでしたか?"
            body = f"""{customer_name}

{prefix}お願いがあります!

{topic}を買ってから2週間経ちましたね。

どうでしたか? 感想を聞かせてもらえると嬉しいです!

【レビュー特典】
- 書いてくれたら500円クーポンプレゼント!
- たった2分で完了

{key_points}

レビューの書き方:
1. マイページにログイン
2. 購入履歴から選択
3. 星とコメントを入力

{ending}"""
        
        elif tone == "説得力をもって積極的に":
            subject = f"【今すぐレビューを!】{topic}の声を聞かせてください"
            body = f"""{customer_name}

あなたの声が必要です!

{topic}をご購入から2週間。ぜひレビューをお願いします!

あなたのレビューが、他のお客様の役に立ちます。
そして、より良い商品開発につながります。

【今だけ特典】
- レビューで500円クーポン
- わずか2分で完了

{key_points}

{ending}"""
        
        else:  # その他のトーン
            subject = f"【{prefix}ご感想をお聞かせください】{topic}のレビューをお願いいたします"
            body = f"""{customer_name}

いつもご利用いただきありがとうございます。

{topic}をご購入から2週間が経過いたしました。

商品のご感想やご意見をお聞かせいただけますと幸いです。
皆様からのレビューは、商品改善の貴重な参考とさせていただいております。

【レビュー特典】
- レビューご投稿で次回使える500円クーポンをプレゼント
- 所要時間はわずか2分程度です

{key_points}

レビューは簡単3ステップ:
1. マイページにログイン
2. ご購入履歴から商品を選択
3. 星評価とコメントを入力

{ending}"""
    
    else:  # step_count >= 5
        # ⑤5回目以降 → 継続的なレビュー依頼
        subject = f"【{prefix}再度のお願い】{topic}のご感想をお聞かせください"
        body = f"""{customer_name}

いつもご利用いただきありがとうございます。

以前、{topic}のレビューをお願いさせていただきましたが、
引き続きご感想をお聞かせいただけますと大変嬉しく存じます。

{key_points}

お客様の声は、私たちにとって何よりも大切な財産です。
簡単なコメントでも構いませんので、ぜひお聞かせください。

【レビュー特典】
次回のお買い物で使える500円クーポンをプレゼント中

{ending}

※既にレビューをいただいている場合は、本メールを無視していただいて結構です。"""
    
    return {
        "status": "success",
        "subject": subject,
        "body": body
    }


def start_mock_lrs():
    """モックLRSサーバーを起動"""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    
    try:
        server.bind((HOST, PORT))
        server.listen(5)
        print(f"[Mock LRS] 起動完了: {HOST}:{PORT}")
        print("[Mock LRS] Ctrl+C で終了")
        
        while True:
            client, addr = server.accept()
            print(f"\n[Mock LRS] 接続: {addr}")
            
            try:
                # リクエスト受信
                data = client.recv(4096).decode('utf-8')
                if not data:
                    continue
                
                request = json.loads(data)
                print(f"[Mock LRS] リクエスト: {request.get('topic')} (ステップ {request.get('step_count')}, トーン: {request.get('tone')})")
                
                # モックレスポンス生成
                response = generate_mock_response(request)
                
                # レスポンス送信
                response_json = json.dumps(response, ensure_ascii=False)
                client.sendall(response_json.encode('utf-8'))
                print(f"[Mock LRS] レスポンス送信: {response['subject'][:40]}...")
                
            except json.JSONDecodeError:
                error_response = json.dumps({
                    "status": "error",
                    "message": "Invalid JSON"
                })
                client.sendall(error_response.encode('utf-8'))
                
            except Exception as e:
                print(f"[Mock LRS] エラー: {e}")
                error_response = json.dumps({
                    "status": "error",
                    "message": str(e)
                }, ensure_ascii=False)
                client.sendall(error_response.encode('utf-8'))
            
            finally:
                client.close()
    
    except KeyboardInterrupt:
        print("\n[Mock LRS] 終了します...")
    
    except Exception as e:
        print(f"[Mock LRS] サーバーエラー: {e}")
        sys.exit(1)
    
    finally:
        server.close()
        print("[Mock LRS] サーバーを閉じました")


if __name__ == '__main__':
    start_mock_lrs()