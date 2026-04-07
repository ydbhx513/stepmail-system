"""
顧客データ管理モジュール（改訂版）
- グループIDは自動採番（0001, 0002...）
- グループ名は不要
"""

import pandas as pd
import json
import os
from datetime import datetime

CUSTOMER_DB_FILE = "customers.json"  # 拡張子を.jsonに変更
GROUPS_DB_FILE = "groups.json"
SCHEDULE_CONFIG_FILE = "schedule_config.json"


class CustomerManager:
    """顧客データとグループを管理するクラス"""
    
    def __init__(self):
        self.customers = []
        self.groups = {}
        self.schedule_config = self.load_schedule_config()
        self.load_groups()
        self.load_customers()  # 初期化時に顧客データをロード
    
    def generate_next_group_id(self):
        """
        次のグループIDを生成（0001, 0002...）
        
        Returns:
            str: 次のグループID
        """
        if not self.groups:
            return "0001"
        
        # 既存のIDから最大値を取得
        existing_ids = [int(gid) for gid in self.groups.keys() if gid.isdigit()]
        if not existing_ids:
            return "0001"
        
        next_id = max(existing_ids) + 1
        return f"{next_id:04d}"  # 0001形式
    
    def load_from_csv(self, csv_path):
        """
        CSVファイルから顧客データを読み込む
        
        必須列:
        - 漢字氏名
        - メールアドレス
        
        Args:
            csv_path: CSVファイルのパス
        
        Returns:
            tuple: (成功フラグ, メッセージ, 顧客リスト)
        """
        try:
            # CSVを読み込み（Shift-JIS）
            df = pd.read_csv(csv_path, encoding='shift-jis')
            
            # 必須列のチェック
            required_cols = ['漢字氏名', 'メールアドレス']
            missing_cols = [col for col in required_cols if col not in df.columns]
            
            if missing_cols:
                return False, f"必須列が見つかりません: {', '.join(missing_cols)}", []
            
            # 既存のデータをメールアドレスをキーにしてマップ化（既存のグループ割り当てを維持するため）
            existing_map = {c['email']: c.get('group') for c in self.customers}
            
            # 顧客データを辞書のリストに変換
            new_customers = []
            for idx, row in df.iterrows():
                name = str(row['漢字氏名']).strip()
                email = str(row['メールアドレス']).strip()
                
                # 空行スキップ
                if pd.isna(name) or pd.isna(email) or name == '' or email == '':
                    continue
                
                # 既存のグループがあれば維持、なければNone
                current_group = existing_map.get(email, None)
                
                new_customers.append({
                    'name': name,
                    'email': email,
                    'group': current_group
                })
            
            if not new_customers:
                return False, "有効な顧客データが見つかりませんでした", []
            
            self.customers = new_customers
            self.save_customers()  # 保存
            return True, f"{len(new_customers)}件の顧客データを読み込みました", new_customers
            
        except Exception as e:
            return False, f"CSVの読み込みエラー: {str(e)}", []
    
    def get_customers(self):
        """全顧客データを取得"""
        return self.customers
    
    def get_customers_by_group(self, group_id):
        """指定グループの顧客を取得"""
        return [c for c in self.customers if c.get('group') == group_id]
    
    def assign_group(self, customer_indices, group_id):
        """
        顧客にグループを割り当て
        
        Args:
            customer_indices: 顧客のインデックスリスト
            group_id: グループID
        """
        changed = False
        for idx in customer_indices:
            if 0 <= idx < len(self.customers):
                self.customers[idx]['group'] = group_id
                changed = True
        
        if changed:
            self.save_customers()  # 変更があったら保存
    
    def unassign_group(self, customer_indices):
        """
        顧客からグループを解除
        
        Args:
            customer_indices: 顧客のインデックスリスト
        """
        changed = False
        for idx in customer_indices:
            if 0 <= idx < len(self.customers):
                self.customers[idx]['group'] = None
                changed = True
        
        if changed:
            self.save_customers()
    
    def clear_group_assignments(self, group_id):
        """
        指定グループに割り当てられている全顧客のグループを解除
        
        Args:
            group_id: グループID
        """
        changed = False
        for customer in self.customers:
            if customer.get('group') == group_id:
                customer['group'] = None
                changed = True
        
        if changed:
            self.save_customers()
    
    def create_group(self, tone, topic, key_points, custom_schedule=None):
        """
        新しいグループを作成（グループIDは自動採番）
        
        Args:
            tone: トーン（リスト）
            topic: 商品名/トピック
            key_points: 詳細ポイント
            custom_schedule: カスタムスケジュール設定（オプション）
        
        Returns:
            str: 作成されたグループID
        """
        group_id = self.generate_next_group_id()
        
        self.groups[group_id] = {
            'group_id': group_id,
            'tone': tone,
            'topic': topic,
            'key_points': key_points,
            'created_at': datetime.now().isoformat(),
            'custom_schedule': custom_schedule  # None または設定辞書
        }
        self.save_groups()
        return group_id
    
    def get_group(self, group_id):
        """グループ情報を取得"""
        return self.groups.get(group_id)
    
    def get_all_groups(self):
        """全グループを取得"""
        return self.groups
    
    def delete_group(self, group_id):
        """グループを削除"""
        if group_id in self.groups:
            del self.groups[group_id]
            # 該当グループに属する顧客のグループをクリア
            changed = False
            for customer in self.customers:
                if customer.get('group') == group_id:
                    customer['group'] = None
                    changed = True
            
            self.save_groups()
            if changed:
                self.save_customers()
    
    def save_groups(self):
        """グループ情報をJSONファイルに保存"""
        try:
            with open(GROUPS_DB_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.groups, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"グループ保存エラー: {e}")
    
    def load_groups(self):
        """グループ情報をJSONファイルから読み込み"""
        try:
            if os.path.exists(GROUPS_DB_FILE):
                with open(GROUPS_DB_FILE, 'r', encoding='utf-8') as f:
                    self.groups = json.load(f)
        except Exception as e:
            print(f"グループ読み込みエラー: {e}")
            self.groups = {}
    
    def get_ungrouped_customers(self):
        """グループ未設定の顧客を取得"""
        return [c for c in self.customers if c.get('group') is None]
    
    def get_group_summary(self):
        """グループごとの顧客数を取得"""
        summary = {}
        for customer in self.customers:
            group = customer.get('group', '未設定')
            summary[group] = summary.get(group, 0) + 1
        return summary
    
    # ===== スケジュール設定関連 =====
    
    def load_schedule_config(self):
        """スケジュール設定を読み込み"""
        try:
            if os.path.exists(SCHEDULE_CONFIG_FILE):
                with open(SCHEDULE_CONFIG_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            print(f"スケジュール設定読み込みエラー: {e}")
        
        # デフォルト設定
        return {
            "step1_days": 0,      # サンクスメール: 当日
            "step2_days": 2,      # 商品紹介: 2日後
            "step3_days": 7,      # おすすめ商品: 7日後
            "step4_days": 14,     # レビュー依頼: 14日後
            "send_time": "10:00"  # 送信時刻
        }
    
    def save_schedule_config(self, config):
        """スケジュール設定を保存"""
        try:
            with open(SCHEDULE_CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            self.schedule_config = config
        except Exception as e:
            print(f"スケジュール設定保存エラー: {e}")
    
    def get_schedule_config(self):
        """スケジュール設定を取得"""
        return self.schedule_config
    
    def get_group_schedule(self, group_id):
        """
        グループのスケジュール設定を取得
        カスタム設定があればそれを、なければデフォルトを返す
        
        Args:
            group_id: グループID
        
        Returns:
            dict: スケジュール設定
        """
        group = self.get_group(group_id)
        if group and group.get('custom_schedule'):
            return group['custom_schedule']
        return self.schedule_config
    
    # ===== 顧客データの永続化メソッド =====
    
    def save_customers(self):
        """顧客データをJSONファイルに保存"""
        try:
            with open(CUSTOMER_DB_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.customers, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"顧客データ保存エラー: {e}")
    
    def load_customers(self):
        """顧客データをJSONファイルから読み込み"""
        try:
            if os.path.exists(CUSTOMER_DB_FILE):
                with open(CUSTOMER_DB_FILE, 'r', encoding='utf-8') as f:
                    self.customers = json.load(f)
            else:
                self.customers = []
        except Exception as e:
            print(f"顧客データ読み込みエラー: {e}")
            self.customers = []