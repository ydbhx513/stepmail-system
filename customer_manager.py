"""
顧客データ管理モジュール
CSVから顧客データを読み込み、グループごとに管理します
（修正版：顧客データの永続化機能を追加）
"""

import pandas as pd
import json
import os

# 拡張子を .json に変更して扱いやすくします
CUSTOMER_DB_FILE = "customers.json"
GROUPS_DB_FILE = "groups.json"


class CustomerManager:
    """顧客データとグループを管理するクラス"""
    
    def __init__(self):
        self.customers = []
        self.groups = {}
        self.load_groups()
        self.load_customers()  # 初期化時に顧客データをロード
    
    def load_from_csv(self, csv_path):
        """
        CSVファイルから顧客データを読み込む
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
    
    def get_customers_by_group(self, group_name):
        """指定グループの顧客を取得"""
        return [c for c in self.customers if c.get('group') == group_name]
    
    def assign_group(self, customer_indices, group_name):
        """顧客にグループを割り当て"""
        changed = False
        for idx in customer_indices:
            if 0 <= idx < len(self.customers):
                self.customers[idx]['group'] = group_name
                changed = True
        
        if changed:
            self.save_customers()  # 変更があったら保存
    
    def unassign_group(self, customer_indices):
        """顧客からグループを解除"""
        changed = False
        for idx in customer_indices:
            if 0 <= idx < len(self.customers):
                self.customers[idx]['group'] = None
                changed = True
        
        if changed:
            self.save_customers()
    
    def clear_group_assignments(self, group_name):
        """指定グループに割り当てられている全顧客のグループを解除"""
        changed = False
        for customer in self.customers:
            if customer.get('group') == group_name:
                customer['group'] = None
                changed = True
        
        if changed:
            self.save_customers()
    
    def create_group(self, group_name, tone, topic, key_points):
        """新しいグループを作成"""
        self.groups[group_name] = {
            'tone': tone,
            'topic': topic,
            'key_points': key_points,
            'created_at': pd.Timestamp.now().isoformat()
        }
        self.save_groups()
    
    def get_group(self, group_name):
        """グループ情報を取得"""
        return self.groups.get(group_name)
    
    def get_all_groups(self):
        """全グループを取得"""
        return self.groups
    
    def delete_group(self, group_name):
        """グループを削除"""
        if group_name in self.groups:
            del self.groups[group_name]
            # 該当グループに属する顧客のグループをクリア
            changed = False
            for customer in self.customers:
                if customer.get('group') == group_name:
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

    # --- 新規追加: 顧客データの永続化メソッド ---

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


if __name__ == "__main__":
    # テスト用
    manager = CustomerManager()
    
    # サンプルCSV作成
    sample_data = pd.DataFrame({
        '漢字氏名': ['山田太郎', '佐藤花子', '鈴木一郎'],
        'メールアドレス': ['yamada@example.com', 'sato@example.com', 'suzuki@example.com']
    })
    sample_data.to_csv('sample_customers.csv', index=False, encoding='utf-8-sig')
    
    # テスト
    success, message, customers = manager.load_from_csv('sample_customers.csv')
    print(message)
    
    # グループ割り当てテスト
    manager.create_group("テストグループ", "丁寧", "うどん", "美味しい")
    manager.assign_group([0, 1], "テストグループ")
    print("保存後のグループ割り当て:", manager.get_customers_by_group("テストグループ"))
    
    # 再読み込みテスト（永続化確認）
    manager2 = CustomerManager()
    print("再起動後のグループ割り当て:", manager2.get_customers_by_group("テストグループ"))