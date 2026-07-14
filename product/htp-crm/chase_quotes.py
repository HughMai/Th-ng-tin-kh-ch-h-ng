"""
Quote Follow-up Draft Generator
bike-method-phase: 1
three-ms-attribution: Adapted from The Three Ms of AI™ © 2026 Nate Herk.
"""

import sys
import store
import templates_vi

def format_currency(value):
    if not value:
        return "0"
    return f"{value:,.0f}".replace(",", ".")

def main():
    sys.stdout.reconfigure(encoding='utf-8')
    store.configure("data/htp.db")
    quotes = store.quotes_to_chase()
    
    if not quotes:
        print("Không có báo giá nào cần nhắc hôm nay! (No quotes to chase today)")
        return

    print(f"Found {len(quotes)} quote(s) to chase:\n")
    
    for q in quotes:
        nudge_level = q['nudge_level']
        template_key = "quote_followup_1" if nudge_level == 1 else "quote_followup_2"
        san_pham_label = templates_vi.PRODUCT_LABELS.get(q['product'], "sản phẩm")
        
        # We don't necessarily need so_tien and ngay for quote_followup, but if they are needed, render accepts **kw
        msg = templates_vi.render(
            template_key, 
            ten=q['customer_name'], 
            san_pham=san_pham_label,
            so_tien=format_currency(q.get('value_vnd', 0)),
            ngay="" # Not used in quote followup
        )
        
        print(f"--- Báo giá #{q['id']} ({q['customer_name']}) - Nhắc lần {nudge_level} ---")
        print(f"Phone / Zalo: {q['zalo_phone'] or q['phone']}")
        print(f"Value: {format_currency(q.get('value_vnd', 0))} VND")
        print("Draft Message:")
        print(msg)
        print("-" * 60 + "\n")

if __name__ == "__main__":
    main()
