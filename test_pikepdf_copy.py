import pikepdf
from pikepdf import Pdf, Dictionary

p1 = Pdf.new()
p1.add_blank_page(page_size=(100, 100))
p2 = Pdf.new()
p2.add_blank_page(page_size=(100, 100))

obj1 = Dictionary({"/A": 1})
ref1 = p1.make_indirect(obj1)
ref2 = p2.copy_foreign(ref1)
ref3 = p2.copy_foreign(ref1)
print("Are they the same?", ref2.objgen == ref3.objgen)
