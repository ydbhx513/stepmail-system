"""
田舎主義ステップメール生成システム
"""

import streamlit as st
import socket
import json
import sys
import webbrowser
import urllib.parse
import db_manager
import os
import subprocess
from datetime import datetime, timedelta
from customer_manager import CustomerManager
from product_manager import ProductManager

# 定数
LRS_HOST = "localhost"
LRS_PORT = 5000


def send_to_lrs(request_data):
    """LRSサーバーにリクエストを送信（LLM使用時）"""
    try:
        client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client_socket.settimeout(600)
        client_socket.connect((LRS_HOST, LRS_PORT))
        
        request_json = json.dumps(request_data, ensure_ascii=False)
        client_socket.sendall(request_json.encode("utf-8"))
        
        response_data = client_socket.recv(16384).decode("utf-8")
        client_socket.close()
        
        return json.loads(response_data)
        
    except Exception as e:
        return {
            "status": "error",
            "message": f"LRS接続エラー: {str(e)}"
        }


def generate_local_email(request_data):
    """ローカルでメール生成（LLM不使用時）"""
    try:
        from mock_lrs import generate_mock_response
        return generate_mock_response(request_data)
    except Exception as e:
        return {
            "status": "error",
            "message": f"ローカル生成エラー: {str(e)}"
        }


def generate_batch_emails_for_group(group_config, product_manager, selected_tones, mail_type, use_llm=False):
    """グループの設定に基づいて複数トーン × ステップメール4回分を一括生成"""
    results = {}
    
    # メール生成関数を選択
    generate_func = send_to_lrs if use_llm else generate_local_email
    
    topic = group_config['topic']
    key_points = group_config['key_points']
    
    # 関連商品を取得
    related_products = product_manager.get_related_products(topic)
    
    for tone in selected_tones:
        if mail_type == "direct":
            # ダイレクトメールの場合は1通のみ
            request_data = {
                "customer_name": "お客様",
                "topic": topic,
                "key_points": key_points,
                "tone": tone,
                "mail_type": "direct",
                "step_count": 1,
                "related_products": related_products if related_products else None
            }
            response = generate_func(request_data)
            
            if response.get("status") == "success":
                results[tone] = [response]
            else:
                results[tone] = [{"status": "error", "message": response.get("message")}]
        else:
            # ステップメールの場合は4通生成
            step_mails = []
            for step in range(1, 5):
                request_data = {
                    "customer_name": "お客様",
                    "topic": topic,
                    "key_points": key_points,
                    "tone": tone,
                    "mail_type": "step",
                    "step_count": step,
                    "related_products": related_products if step == 3 and related_products else None
                }
                response = generate_func(request_data)
                step_mails.append(response)
            
            results[tone] = step_mails
    
    return results


def open_mail_clients(recipients, subject, body):
    """複数の宛先に対してメールクライアントを起動"""
    for recipient in recipients:
        encoded_subject = urllib.parse.quote(subject)
        encoded_body = urllib.parse.quote(body)
        mailto_url = f"mailto:{recipient}?subject={encoded_subject}&body={encoded_body}"
        webbrowser.open(mailto_url)


def create_scheduled_email_task(task_name, scheduled_datetime, recipient, subject, body):
    """Windowsタスクスケジューラーにメール送信タスクを登録"""
    try:
        ps_script_path = os.path.join(os.getcwd(), f"{task_name}.ps1")
        
        ps_script_content = f"""
$outlook = New-Object -ComObject Outlook.Application
$mail = $outlook.CreateItem(0)
$mail.To = "{recipient}"
$mail.Subject = "{subject}"
$mail.Body = @"
{body}
"@
$mail.Send()
Write-Host "メール送信完了: {recipient}"
exit 0
"""
        
        with open(ps_script_path, 'w', encoding='utf-8') as f:
            f.write(ps_script_content)
        
        task_datetime = scheduled_datetime.strftime("%Y/%m/%d %H:%M")
        cmd = f'''schtasks /Create /TN "{task_name}" /TR "powershell.exe -ExecutionPolicy Bypass -File \\"{ps_script_path}\\"" /SC ONCE /SD {scheduled_datetime.strftime("%Y/%m/%d")} /ST {scheduled_datetime.strftime("%H:%M")} /F'''
        
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        
        if result.returncode == 0:
            return True, f"タスク '{task_name}' を登録しました（送信予定: {task_datetime}）"
        else:
            return False, f"タスク登録エラー: {result.stderr}"
            
    except Exception as e:
        return False, f"タスク登録エラー: {str(e)}"


def schedule_all_step_emails(customers, edited_mails, scheduled_dates, group_name):
    """ステップメール4通をすべてWindowsタスクスケジューラーに登録"""
    success_count = 0
    fail_count = 0
    messages = []
    
    step_names = ["サンクスメール", "商品紹介", "おすすめ商品", "レビュー依頼"]
    
    for customer in customers:
        customer_name = customer['name']
        recipient = customer['email']
        
        for idx, (edited_mail, scheduled_dt, step_name) in enumerate(zip(edited_mails, scheduled_dates, step_names), 1):
            # 宛名を先頭に追加
            subject = edited_mail["subject"]
            body = f"{customer_name} 様\n\n{edited_mail['body']}"
            
            # タスク名を生成（ユニーク）
            task_name = f"EmailSend_{group_name.replace(' ', '')}_{customer_name.replace(' ', '')}_{recipient.split('@')[0]}_Step{idx}_{scheduled_dt.strftime('%Y%m%d%H%M')}"
            
            # タスク登録
            success, message = create_scheduled_email_task(
                task_name=task_name,
                scheduled_datetime=scheduled_dt,
                recipient=recipient,
                subject=subject,
                body=body
            )
            
            if success:
                success_count += 1
                messages.append(f"✅ {customer_name} ({recipient}) - {step_name}: 登録成功")
            else:
                fail_count += 1
                messages.append(f"❌ {customer_name} ({recipient}) - {step_name}: {message}")
    
    return success_count, fail_count, messages


def main():
    st.set_page_config(
        page_title="田舎主義ステップメール生成システム",
        page_icon="📧",
        layout="wide"
    )
    
    st.title("📧 田舎主義ステップメール生成システム")
    st.markdown("---")
    
    # マネージャーの初期化
    if 'customer_manager' not in st.session_state:
        st.session_state.customer_manager = CustomerManager()
    
    if 'product_manager' not in st.session_state:
        st.session_state.product_manager = ProductManager()
    
    customer_manager = st.session_state.customer_manager
    product_manager = st.session_state.product_manager
    
    # サイドバー: 関連商品管理
    with st.sidebar:
        st.header("⚙️ 設定")
        
        with st.expander("🔗 関連商品管理", expanded=False):
            st.markdown("### 関連商品の登録")
            
            # 既存の関連商品表示
            all_products = product_manager.get_all_products()
            if all_products:
                st.markdown("#### 登録済み商品")
                for product, related in all_products.items():
                    with st.expander(f"📦 {product}"):
                        st.write("**関連商品:**")
                        for r in related:
                            st.write(f"- {r}")
                        if st.button(f"削除", key=f"del_product_{product}"):
                            product_manager.delete_product(product)
                            st.success(f"'{product}' を削除しました")
                            st.rerun()
            
            st.markdown("---")
            st.markdown("#### 新規登録")
            
            new_product = st.text_input("商品名", key="new_product_name")
            related_1 = st.text_input("関連商品1", key="related_1")
            related_2 = st.text_input("関連商品2", key="related_2")
            related_3 = st.text_input("関連商品3", key="related_3")
            
            if st.button("関連商品を登録"):
                if new_product:
                    related_list = [r for r in [related_1, related_2, related_3] if r]
                    if related_list:
                        product_manager.add_product_relation(new_product, related_list)
                        st.success(f"'{new_product}' の関連商品を登録しました")
                        st.rerun()
                    else:
                        st.warning("関連商品を少なくとも1つ入力してください")
                else:
                    st.warning("商品名を入力してください")
    
    # ===== ステップ1: CSVアップロード =====
    st.header("ステップ1: 顧客データをCSVからアップロード")
    
    st.info("""
    📋 **CSVファイルの形式**
    
    必須列:
    - `漢字氏名`: 顧客の氏名（例: 山田太郎）
    - `メールアドレス`: 顧客のメールアドレス（例: yamada@example.com）
    
    文字コード: **Shift-JIS**  
    1行目に列名を含むCSVファイルをアップロードしてください。
    """)
    
    uploaded_file = st.file_uploader(
        "CSVファイルを選択",
        type=['csv'],
        help="漢字氏名とメールアドレスの列を含むCSVファイル（Shift-JIS）"
    )
    
    if uploaded_file is not None:
        # 一時ファイルとして保存
        temp_csv_path = f"/tmp/{uploaded_file.name}"
        with open(temp_csv_path, 'wb') as f:
            f.write(uploaded_file.getbuffer())
        
        # CSVを読み込み
        success, message, customers = customer_manager.load_from_csv(temp_csv_path)
        
        if success:
            st.success(message)
        else:
            st.error(message)
    
    st.markdown("---")
    
    # ===== ステップ2: 送信グループの作成 =====
    st.header("ステップ2: 送信グループの作成")
    
    if not customer_manager.get_customers():
        st.warning("⚠️ 先に顧客データをアップロードしてください")
    else:
        st.markdown("### ➕ 新規グループ作成")
        
        col1, col2 = st.columns(2)
        
        with col1:
            new_group_name = st.text_input(
                "グループ名",
                placeholder="例: VIP顧客、新規顧客、リピーター",
                key="new_group_name"
            )
        
        with col2:
            # 複数トーン選択対応
            new_group_tones = st.multiselect(
                "トーン（複数選択可）",
                [
                    "丁寧かつ熱意をもって",
                    "フォーマルで厳格に",
                    "フレンドリーで親しみやすく",
                    "簡潔で要点を押さえて",
                    "説得力をもって積極的に",
                    "控えめで謙虚に",
                    "情熱的でエネルギッシュに"
                ],
                default=["丁寧かつ熱意をもって"],
                key="new_group_tones"
            )
        
        new_group_topic = st.text_input(
            "商品名/トピック",
            placeholder="例: 讃岐うどん",
            key="new_group_topic"
        )
        
        new_group_key_points = st.text_area(
            "商品の詳細・特徴",
            placeholder="例: 本場讃岐の味、コシが強い、無添加",
            height=100,
            key="new_group_key_points"
        )
        
        st.info("💡 グループ作成後、ステップ3で顧客を割り当てることができます")
        
        if st.button("✅ グループを作成", type="primary", use_container_width=True):
            if new_group_name and new_group_topic and new_group_tones:
                # グループ作成（顧客は未割り当て）
                customer_manager.create_group(
                    new_group_name,
                    new_group_tones,  # リストで保存
                    new_group_topic,
                    new_group_key_points
                )
                
                st.success(f"グループ '{new_group_name}' を作成しました。ステップ3で顧客を割り当ててください。")
                st.rerun()
            else:
                if not new_group_tones:
                    st.warning("トーンを少なくとも1つ選択してください")
                else:
                    st.warning("グループ名と商品名は必須です")
    
    st.markdown("---")
    
    # ===== ステップ3: 既存グループ管理 =====
    st.header("ステップ3: 既存グループ管理")
    
    all_groups = customer_manager.get_all_groups()
    
    if not all_groups:
        st.info("まだグループが作成されていません。ステップ2でグループを作成してください。")
    else:
        st.markdown("### 📋 既存グループ一覧")
        
        for group_name, group_config in all_groups.items():
            with st.expander(f"📁 {group_name}", expanded=True):
                # トーンがリストかどうか確認
                tones = group_config.get('tone', [])
                if isinstance(tones, str):
                    # 旧形式（文字列）の場合はリストに変換
                    tones = [tones]
                
                tones_str = "、".join(tones) if tones else "未設定"

                # 作成日時のフォーマット変換
                created_at = group_config.get('created_at', 'N/A')
                if created_at != 'N/A':
                    try:
                        dt = datetime.fromisoformat(created_at)
                        created_at = dt.strftime('%Y-%m-%d %H:%M:%S')
                    except:
                        pass  # フォーマット変換に失敗した場合は元の値を使用

                st.markdown(f"""
                **トーン:** {tones_str}  
                **商品名:** {group_config['topic']}  
                **詳細:** {group_config['key_points']}  
                **作成日時:** {created_at}
                """)
                
                st.markdown("---")
                
                # 顧客割り当てセクション
                st.markdown("#### 👥 顧客の割り当て")
                
                all_customers = customer_manager.get_customers()
                if all_customers:
                    # 現在このグループに割り当てられている顧客のインデックスを取得
                    current_assigned = [
                        i for i, c in enumerate(all_customers)
                        if c.get('group') == group_name
                    ]
                    
                    group_key = f"group_{hash(group_name)}"

                    selected_customers = st.multiselect(
                        "顧客を選択（複数選択可）",
                        options=range(len(all_customers)),
                        format_func=lambda i: f"{all_customers[i]['name']} ({all_customers[i]['email']})",
                        default=current_assigned,
                        key=f"{group_key}_assign"
                    )
                    
                    # 更新成功メッセージの表示（rerun後に表示）
                    success_key = f'update_success_{group_name}'
                    if success_key in st.session_state:
                        count = st.session_state[success_key]
                        st.success(f"✅ {count}名の顧客を '{group_name}' に割り当てました")
                        del st.session_state[success_key]
                    
                    # このグループの顧客を表示（最新の状態を取得）
                    group_customers = customer_manager.get_customers_by_group(group_name)
                    
                    st.markdown(f"**現在の顧客数:** {len(group_customers)}名")
                    
                    if group_customers:
                        st.markdown("**割り当て済み顧客:**")
                        for customer in group_customers:
                            st.text(f"- {customer['name']} ({customer['email']})")
                    
                    col1, col2, col3 = st.columns([2, 2, 1])
                    
                    with col1:
                        if st.button("💾 顧客割り当てを更新", key=f"{group_key}_update"):
                            # 顧客割り当てを更新
                            customer_manager.clear_group_assignments(group_name)
                            customer_manager.assign_group(selected_customers, group_name)
                            
                            # multiselectのキーをクリア（rerun後に最新のdefaultが適用される）
                            multiselect_key = f"{group_key}_assign"
                            if multiselect_key in st.session_state:
                                del st.session_state[multiselect_key]
                            
                            # 成功メッセージをセッションステートに保存してrerun
                            updated_customers = customer_manager.get_customers_by_group(group_name)
                            st.session_state[success_key] = len(updated_customers)
                            st.rerun()
                    
                    with col2:
                        # メール生成ボタン（顧客が割り当てられている場合のみ有効）
                        # group_customersを使用（上で既に取得済み）
                        if len(group_customers) > 0:
                            if st.button("✉️ メール生成", key=f"generate_group_{group_name}", type="primary"):
                                st.session_state.selected_group_for_generation = group_name
                                st.rerun()
                        else:
                            st.button("✉️ メール生成", key=f"generate_group_{group_name}", disabled=True)
                            st.caption("顧客を割り当ててください")
                    
                    with col3:
                        if st.button("🗑️ 削除", key=f"delete_group_{group_name}"):
                            customer_manager.delete_group(group_name)
                            st.success(f"グループ '{group_name}' を削除しました")
                            st.rerun()
                else:
                    st.warning("顧客データがありません。ステップ1でCSVをアップロードしてください。")
    
    st.markdown("---")
    
    # ===== ステップ4: グループ選択とメール生成 =====
    if 'selected_group_for_generation' in st.session_state:
        group_name = st.session_state.selected_group_for_generation
        group_config = customer_manager.get_group(group_name)
        group_customers = customer_manager.get_customers_by_group(group_name)
        
        if not group_config:
            st.error("グループ情報が見つかりません")
        elif not group_customers:
            st.error(f"グループ '{group_name}' に顧客が割り当てられていません。ステップ2で顧客を追加してください。")
        else:
            st.header(f"ステップ4: グループ '{group_name}' のメール生成")
            
            # トーンがリストかどうか確認
            tones = group_config.get('tone', [])
            if isinstance(tones, str):
                # 旧形式（文字列）の場合はリストに変換
                tones = [tones]
            
            tones_str = "、".join(tones) if tones else "未設定"
            
            st.info(f"""
            **グループ情報:**
            - 顧客数: {len(group_customers)}名
            - トーン: {tones_str}
            - 商品名: {group_config['topic']}
            """)
            
            # メールタイプ選択
            mail_type_option = st.radio(
                "メールタイプを選択",
                ["ステップメール（4通）", "ダイレクトメール（1通）"],
                key="mail_type_selection"
            )
            
            is_step_mail = "ステップ" in mail_type_option
            
            # LLM使用オプション
            use_llm = st.checkbox(
                "🤖 LLMを使用する（lrs_service.pyが起動している必要があります）",
                value=False,
                key="use_llm_option"
            )
            
            if st.button("📝 メール生成開始", type="primary", use_container_width=True):
                with st.spinner("メールを生成中..."):
                    # グループのトーン（複数）で生成
                    selected_tones = tones if tones else ["丁寧かつ熱意をもって"]
                    
                    # メール生成
                    generated_mails = generate_batch_emails_for_group(
                        group_config=group_config,
                        product_manager=product_manager,
                        selected_tones=selected_tones,
                        mail_type="step" if is_step_mail else "direct",
                        use_llm=use_llm
                    )
                    
                    # セッションステートに保存
                    st.session_state.generated_mails = generated_mails
                    st.session_state.current_group = group_name
                    st.session_state.is_step_mail = is_step_mail
                    
                st.success("✅ メール生成完了!")
                st.rerun()
    
    # ===== ステップ5: ステップメールプレビュー（複数トーン対応） =====
    if "generated_mails" in st.session_state and st.session_state.is_step_mail:
        st.divider()
        st.header("ステップ5: ステップメールプレビュー")
        
        group_name = st.session_state.current_group
        group_customers = customer_manager.get_customers_by_group(group_name)
        generated_mails = st.session_state.generated_mails
        
        st.info(f"📌 グループ: **{group_name}** / 顧客数: **{len(group_customers)}名**")
        
        # 顧客選択（プレビュー用）
        if group_customers:
            st.markdown("##### 👤 プレビュー用顧客選択")
            customer_options = [f"{c['name']} ({c['email']})" for c in group_customers]
            selected_customer_idx = st.selectbox(
                "メールのプレビューを表示する顧客を選択",
                range(len(group_customers)),
                format_func=lambda i: customer_options[i],
                key="preview_customer_step"
            )
            preview_customer_name = group_customers[selected_customer_idx]['name']
            st.caption("💡 メール送信時に、選択した顧客の名前が宛名として自動挿入されます")
        else:
            preview_customer_name = "顧客名"
        
        # 複数トーンのタブを作成
        tone_tabs = st.tabs([f"トーン: {tone}" for tone in generated_mails.keys()])
        
        for tab, (tone, mails) in zip(tone_tabs, generated_mails.items()):
            with tab:
                # 4通のステップメールを表示
                step_names = ["サンクスメール", "商品紹介", "おすすめ商品", "レビュー依頼"]
                step_timings = ["購入当日（即時）", "購入から2日後", "購入から7日後", "購入から14日後"]
                
                for step_idx, (mail, step_name, timing) in enumerate(zip(mails, step_names, step_timings), 1):
                    with st.expander(f"✉️ {step_idx}通目: {step_name}（{timing}）", expanded=(step_idx == 1)):
                        if mail.get("status") == "success":
                            st.markdown(f"**件名:** {mail.get('subject', 'N/A')}")
                            
                            # 宛名プレビュー（編集不可）
                            st.markdown("**宛名（送信時に自動挿入）:**")
                            st.code(f"{preview_customer_name} 様", language="text")
                            
                            # 本文から「お客様」を削除してプレビュー
                            original_body = mail.get("body", "")
                            body_without_salutation = original_body.replace("お客様\n\n", "").replace("お客様\n", "").replace("お客様", "", 1)
                            
                            st.text_area(
                                "本文",
                                value=body_without_salutation,
                                height=220,
                                key=f"preview_body_{tone}_{step_idx}",
                                disabled=True
                            )
                        else:
                            st.error(f"エラー: {mail.get('message')}")
                
                # トーンごとの確定ボタン
                if st.button(f"✅ このトーン「{tone}」で確定", type="primary", use_container_width=True, key=f"confirm_{tone}"):
                    # 本文から「お客様」を削除して保存
                    cleaned_mails = []
                    for mail in mails:
                        if mail.get("status") == "success":
                            original_body = mail.get("body", "")
                            cleaned_body = original_body.replace("お客様\n\n", "").replace("お客様\n", "").replace("お客様", "", 1)
                            cleaned_mails.append({
                                "status": mail.get("status"),
                                "subject": mail.get("subject"),
                                "body": cleaned_body
                            })
                        else:
                            cleaned_mails.append(mail)
                    
                    st.session_state.selected_pattern = {
                        'tone': tone,
                        'mails': cleaned_mails
                    }
                    st.rerun()
        
        # 最初からボタン
        if st.button("🔄 最初から", use_container_width=True, key="reset_step_mail_preview"):
            for key in ["generated_mails", "selected_group_for_generation", "current_group", "is_step_mail"]:
                if key in st.session_state:
                    del st.session_state[key]
            st.rerun()
    
    # ===== ステップ5: ダイレクトメールプレビュー（複数トーン対応） =====
    if "generated_mails" in st.session_state and not st.session_state.is_step_mail:
        st.divider()
        st.header("ステップ5: ダイレクトメールプレビュー")
        
        group_name = st.session_state.current_group
        group_customers = customer_manager.get_customers_by_group(group_name)
        generated_mails = st.session_state.generated_mails
        
        st.info(f"📌 グループ: **{group_name}** / 顧客数: **{len(group_customers)}名**")
        
        # 顧客選択（プレビュー用）
        if group_customers:
            st.markdown("##### 👤 プレビュー用顧客選択")
            customer_options = [f"{c['name']} ({c['email']})" for c in group_customers]
            selected_customer_idx = st.selectbox(
                "メールのプレビューを表示する顧客を選択",
                range(len(group_customers)),
                format_func=lambda i: customer_options[i],
                key="preview_customer_direct"
            )
            preview_customer_name = group_customers[selected_customer_idx]['name']
            st.caption("💡 メール送信時に、選択した顧客の名前が宛名として自動挿入されます")
        else:
            preview_customer_name = "顧客名"
        
        # 複数トーンのタブを作成
        tone_tabs = st.tabs([f"トーン: {tone}" for tone in generated_mails.keys()])
        
        for tab, (tone, mails) in zip(tone_tabs, generated_mails.items()):
            with tab:
                mail = mails[0]
                
                if mail.get("status") == "success":
                    st.markdown(f"**件名:** {mail.get('subject', 'N/A')}")
                    
                    # 宛名プレビュー（編集不可）
                    st.markdown("**宛名（送信時に自動挿入）:**")
                    st.code(f"{preview_customer_name} 様", language="text")
                    
                    # 本文から「お客様」を削除してプレビュー
                    original_body = mail.get("body", "")
                    body_without_salutation = original_body.replace("お客様\n\n", "").replace("お客様\n", "").replace("お客様", "", 1)
                    
                    st.text_area(
                        "本文",
                        value=body_without_salutation,
                        height=350,
                        key=f"preview_direct_body_{tone}",
                        disabled=True
                    )
                    
                    if st.button(f"✅ このトーン「{tone}」で確定", type="primary", use_container_width=True, key=f"confirm_direct_{tone}"):
                        # 本文から「お客様」を削除して保存
                        cleaned_body = original_body.replace("お客様\n\n", "").replace("お客様\n", "").replace("お客様", "", 1)
                        
                        st.session_state.selected_direct_mail = {
                            'tone': tone,
                            'mail': {
                                "status": mail.get("status"),
                                "subject": mail.get("subject"),
                                "body": cleaned_body
                            }
                        }
                        st.rerun()
                else:
                    st.error(f"エラー: {mail.get('message')}")
        
        # 最初からボタン
        if st.button("🔄 最初から", use_container_width=True, key="reset_direct_mail_preview"):
            for key in ["generated_mails", "selected_group_for_generation", "current_group", "is_step_mail"]:
                if key in st.session_state:
                    del st.session_state[key]
            st.rerun()
    
    # ===== ステップ6: ダイレクトメール編集・送信 =====
    if "selected_direct_mail" in st.session_state and not st.session_state.is_step_mail:
        st.divider()
        st.subheader("ステップ6: メール編集・送信")
        
        group_name = st.session_state.current_group
        group_customers = customer_manager.get_customers_by_group(group_name)
        
        mail_data = st.session_state.selected_direct_mail
        st.info(f"📌 グループ: **{group_name}** / トーン: **{mail_data['tone']}** / 顧客数: **{len(group_customers)}名**")
        
        # 顧客選択（プレビュー用）
        if group_customers:
            st.markdown("##### 👤 プレビュー用顧客選択")
            customer_options = [f"{c['name']} ({c['email']})" for c in group_customers]
            selected_customer_idx = st.selectbox(
                "メールのプレビューを表示する顧客を選択",
                range(len(group_customers)),
                format_func=lambda i: customer_options[i],
                key="edit_preview_customer_direct"
            )
            preview_customer_name = group_customers[selected_customer_idx]['name']
        else:
            preview_customer_name = "顧客名"
        
        # 編集可能フォーム
        edited_subject = st.text_input(
            "件名（編集可能）",
            value=mail_data['mail'].get("subject", ""),
            key="edit_direct_subject"
        )
        
        # 宛名プレビュー（編集不可）
        st.markdown("**本文:**")
        st.caption("💡 メール送信時に自動的に「{顧客名} 様」が先頭に追加されます")
        st.markdown("**宛名（送信時に自動挿入）:**")
        st.code(f"{preview_customer_name} 様", language="text")
        
        # 本文編集（宛名は含めない）
        edited_body = st.text_area(
            "本文内容（編集可能）",
            value=mail_data['mail'].get("body", ""),
            height=320,
            key="edit_direct_body",
            help="宛名（{顧客名} 様）は送信時に自動的に先頭に追加されます。こちらには本文のみを入力してください。"
        )
        
        st.info(f"送信先: {len(group_customers)}名の顧客に送信されます")
        
        col1, col2 = st.columns([3, 1])
        
        with col1:
            if st.button("📧 メールクライアント起動", type="primary", use_container_width=True):
                for customer in group_customers:
                    # 宛名を先頭に追加
                    personalized_body = f"{customer['name']} 様\n\n{edited_body}"
                    open_mail_clients([customer['email']], edited_subject, personalized_body)
                
                st.success(f"✅ {len(group_customers)}件のメールクライアントを起動しました")
                st.balloons()
        
        with col2:
            if st.button("🔄 最初から", use_container_width=True, key="reset_direct_mail_edit"):
                for key in ["generated_mails", "selected_direct_mail", "selected_group_for_generation", "current_group", "is_step_mail"]:
                    if key in st.session_state:
                        del st.session_state[key]
                st.rerun()
    
    # ===== ステップ6: ステップメール編集・送信予約 =====
    if "selected_pattern" in st.session_state and st.session_state.is_step_mail:
        st.divider()
        st.subheader("ステップ6: メール編集・送信予定設定")
        
        group_name = st.session_state.current_group
        group_customers = customer_manager.get_customers_by_group(group_name)
        
        pattern = st.session_state.selected_pattern
        st.info(f"📌 グループ: **{group_name}** / トーン: **{pattern['tone']}** / 顧客数: **{len(group_customers)}名**")
        
        # 顧客選択（プレビュー用）
        if group_customers:
            st.markdown("##### 👤 プレビュー用顧客選択")
            customer_options = [f"{c['name']} ({c['email']})" for c in group_customers]
            selected_customer_idx = st.selectbox(
                "メールのプレビューを表示する顧客を選択",
                range(len(group_customers)),
                format_func=lambda i: customer_options[i],
                key="edit_preview_customer_step"
            )
            preview_customer_name = group_customers[selected_customer_idx]['name']
            st.caption("💡 メール送信時に、各顧客の名前が宛名として自動挿入されます")
        else:
            preview_customer_name = "顧客名"
        
        # 送信予定日時のデフォルト値を計算
        if "scheduled_dates" not in st.session_state:
            today = datetime.now()
            st.session_state.scheduled_dates = [
                today,
                today + timedelta(days=2),
                today + timedelta(days=7),
                today + timedelta(days=14)
            ]
        
        # ステップメール4通を編集
        step_names = ["サンクスメール", "商品紹介", "おすすめ商品", "レビュー依頼"]
        step_timings = ["購入当日（即時）", "購入から2日後", "購入から7日後", "購入から14日後"]
        
        # 編集用のセッションステート初期化
        if "edited_mails" not in st.session_state:
            st.session_state.edited_mails = []
            for mail in pattern['mails']:
                st.session_state.edited_mails.append({
                    "subject": mail.get("subject", ""),
                    "body": mail.get("body", "")
                })
        
        for step_idx, (mail, step_name, timing) in enumerate(zip(pattern['mails'], step_names, step_timings), 1):
            with st.expander(f"✉️ {step_idx}通目: {step_name}（{timing}）", expanded=(step_idx == 1)):
                
                # 送信予定日時設定
                col_date, col_time = st.columns(2)
                
                with col_date:
                    scheduled_date = st.date_input(
                        f"送信予定日",
                        value=st.session_state.scheduled_dates[step_idx - 1].date(),
                        key=f"schedule_date_{step_idx}"
                    )
                
                with col_time:
                    scheduled_time = st.time_input(
                        f"送信予定時刻",
                        value=st.session_state.scheduled_dates[step_idx - 1].time(),
                        key=f"schedule_time_{step_idx}"
                    )
                
                # 送信予定日時を更新
                scheduled_datetime = datetime.combine(scheduled_date, scheduled_time)
                st.session_state.scheduled_dates[step_idx - 1] = scheduled_datetime
                
                st.info(f"📅 送信予定: {scheduled_datetime.strftime('%Y年%m月%d日 %H:%M')}")
                
                # メール編集
                if mail.get("status") == "success":
                    edited_subject = st.text_input(
                        "件名（編集可能）",
                        value=st.session_state.edited_mails[step_idx - 1]["subject"],
                        key=f"edit_subject_{step_idx}"
                    )
                    
                    # 宛名プレビュー（編集不可）
                    st.markdown("**本文:**")
                    st.caption("💡 メール送信時に自動的に「{顧客名} 様」が先頭に追加されます")
                    st.markdown("**宛名（送信時に自動挿入）:**")
                    st.code(f"{preview_customer_name} 様", language="text")
                    
                    # 本文編集
                    edited_body = st.text_area(
                        "本文内容（編集可能）",
                        value=st.session_state.edited_mails[step_idx - 1]["body"],
                        height=250,
                        key=f"edit_body_{step_idx}",
                        help="宛名（{顧客名} 様）は送信時に自動的に先頭に追加されます。"
                    )
                    
                    # 編集内容を保存
                    st.session_state.edited_mails[step_idx - 1] = {
                        "subject": edited_subject,
                        "body": edited_body
                    }
                else:
                    st.error(f"エラー: {mail.get('message')}")
        
        st.divider()
        
        # 送信予定スケジュール表示
        st.markdown("### 📋 送信スケジュール確認")
        
        st.info(f"""
        ℹ️ **Windowsタスクスケジューラー自動登録**
        
        グループ '{group_name}' の {len(group_customers)}名の顧客全員に対して、
        指定した日時にOutlookから自動的にメールが送信されるよう設定されます。
        
        合計タスク数: {len(group_customers) * 4}件（{len(group_customers)}名 × 4通）
        """)
        
        import pandas as pd
        schedule_table = []
        for idx, (step_name, scheduled_dt) in enumerate(zip(step_names, st.session_state.scheduled_dates), 1):
            schedule_table.append({
                "ステップ": f"{idx}通目",
                "種類": step_name,
                "送信予定日時": scheduled_dt.strftime("%Y/%m/%d %H:%M"),
                "件名": st.session_state.edited_mails[idx - 1]["subject"][:30] + "..."
            })
        
        st.table(pd.DataFrame(schedule_table))
        
        # 送信方法選択
        col1, col2 = st.columns([2, 1])
        
        with col1:
            send_method = st.radio(
                "送信方法",
                ["タスクスケジューラーに登録（自動送信）", "メールクライアントで確認（手動送信）"],
                key="send_method"
            )
        
        with col2:
            if st.button("🔄 最初から", use_container_width=True, key="reset_step_mail_edit"):
                for key in ["generated_mails", "selected_pattern", "selected_group_for_generation", "current_group", "is_step_mail", "scheduled_dates", "edited_mails"]:
                    if key in st.session_state:
                        del st.session_state[key]
                st.rerun()
        
        st.divider()
        
        # 送信ボタン
        if "タスクスケジューラー" in send_method:
            if st.button("⏰ Windowsタスクスケジューラーに登録", type="primary", use_container_width=True):
                with st.spinner("タスクスケジューラーに登録中..."):
                    success_count, fail_count, messages = schedule_all_step_emails(
                        customers=group_customers,
                        edited_mails=st.session_state.edited_mails,
                        scheduled_dates=st.session_state.scheduled_dates,
                        group_name=group_name
                    )
                    
                    total = success_count + fail_count
                    st.success(f"✅ 登録完了: {success_count}/{total}件成功")
                    
                    if fail_count > 0:
                        st.warning(f"⚠️ {fail_count}件失敗しました")
                    
                    with st.expander("📋 登録詳細", expanded=(fail_count > 0)):
                        for message in messages:
                            st.text(message)
                    
                    if success_count > 0:
                        st.balloons()
        else:
            if st.button("📧 すべてのメールクライアントを起動（4通分）", type="primary", use_container_width=True):
                for customer in group_customers:
                    for idx, edited_mail in enumerate(st.session_state.edited_mails, 1):
                        subject = edited_mail["subject"]
                        body = edited_mail["body"]
                        
                        # 宛名を先頭に追加
                        personalized_body = f"{customer['name']} 様\n\n{body}"
                        
                        # 送信予定日時を本文に追記
                        scheduled_dt = st.session_state.scheduled_dates[idx - 1]
                        body_with_schedule = f"【送信予定: {scheduled_dt.strftime('%Y年%m月%d日 %H:%M')}】\n\n{personalized_body}"
                        
                        open_mail_clients([customer['email']], subject, body_with_schedule)
                
                total_emails = len(group_customers) * 4
                st.success(f"✅ {len(group_customers)}名 × 4通 = 合計{total_emails}件のメールクライアントを起動しました")
                st.warning("⚠️ メールクライアントで手動で予約送信設定を行ってください")
                st.balloons()


if __name__ == "__main__":
    db_manager.initialize_db()
    main()
