"""
関連商品管理モジュール
商品ごとに関連商品を管理し、ステップメール3通目で提案する商品を決定します
"""

import json
import os

PRODUCTS_DB_FILE = "related_products.json"


class ProductManager:
    """関連商品を管理するクラス"""
    
    def __init__(self):
        self.related_products = {}
        self.load_products()
    
    def add_product_relation(self, main_product, related_products):
        """
        商品に関連商品を登録
        
        Args:
            main_product: メイン商品名
            related_products: 関連商品のリスト（最大3件推奨）
        """
        self.related_products[main_product] = related_products
        self.save_products()
    
    def get_related_products(self, main_product):
        """
        指定商品の関連商品を取得
        
        Args:
            main_product: メイン商品名
        
        Returns:
            list: 関連商品のリスト（見つからない場合は空リスト）
        """
        return self.related_products.get(main_product, [])
    
    def get_all_products(self):
        """全ての商品情報を取得"""
        return self.related_products
    
    def delete_product(self, main_product):
        """商品を削除"""
        if main_product in self.related_products:
            del self.related_products[main_product]
            self.save_products()
    
    def save_products(self):
        """商品情報をJSONファイルに保存"""
        try:
            with open(PRODUCTS_DB_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.related_products, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"商品情報保存エラー: {e}")
    
    def load_products(self):
        """商品情報をJSONファイルから読み込み"""
        try:
            if os.path.exists(PRODUCTS_DB_FILE):
                with open(PRODUCTS_DB_FILE, 'r', encoding='utf-8') as f:
                    self.related_products = json.load(f)
            else:
                # デフォルトのサンプルデータ
                self.related_products = {
                    "讃岐うどん": [
                        "讃岐しょうゆうどん",
                        "鍋にそのまま半生うどん",
                        "味噌煮込うどん"
                    ],
                    "寒造そうめん": [
                        "讃岐味ひやむぎ",
                        "讃岐味そうめん",
                        "讃岐ざるうどん"
                    ]
                }
                self.save_products()
        except Exception as e:
            print(f"商品情報読み込みエラー: {e}")
            self.related_products = {}
    
    def format_related_products_text(self, main_product):
        """
        関連商品を整形したテキストとして取得（メール本文用）
        
        Args:
            main_product: メイン商品名
        
        Returns:
            str: 整形された関連商品テキスト
        """
        related = self.get_related_products(main_product)
        
        if not related:
            return "（関連商品の登録がありません）"
        
        lines = []
        for idx, product in enumerate(related, 1):
            lines.append(f"◆ {product}")
        
        return "\n".join(lines)


if __name__ == "__main__":
    # テスト用
    manager = ProductManager()
    
    # テスト
    print("登録済み商品:")
    for product, related in manager.get_all_products().items():
        print(f"  {product} → {related}")
    
    print("\n熟成黒にんにくの関連商品:")
    print(manager.format_related_products_text("熟成黒にんにく"))
