(declare-const x Int)(declare-const y Int)(declare-const z Int)
(assert (and (<= 0 x)(<= x 1)(<= 0 y)(<= y 1)(<= 0 z)(<= z 1)(<= (+ x y) 1)))
