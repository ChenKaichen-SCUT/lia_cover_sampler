; 
(set-info :status unknown)
(declare-fun x () Int)
(assert
 (let (($x9 (= x 2)))
(let (($x7 (= x 0)))
(let (($x10 (or $x7 $x9)))
(and $x10)))))
(check-sat)
