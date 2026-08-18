(function(){
  'use strict';
  const lang=(document.documentElement.lang||'pl').toLowerCase().startsWith('en')?'en':'pl';
  const form=document.getElementById('bmi-whtr-form');
  if(!form) return;

  const T=lang==='pl'?{
    required:'Wpisz prawidłowe dodatnie wartości masy ciała, wzrostu i obwodu talii.',
    range:'Sprawdź dane. Kalkulator obsługuje typowe zakresy dla dorosłych: 30–300 kg, 120–230 cm wzrostu i 35–220 cm talii.',
    bmiLabel:{under:'Niedowaga',healthy:'Zakres prawidłowy',over:'Nadwaga',ob1:'Otyłość I stopnia',ob2:'Otyłość II stopnia',ob3:'Otyłość III stopnia'},
    whtrLabel:{low:'Poniżej typowego zakresu',healthy:'Prawidłowy zakres talii/wzrost',increased:'Podwyższony stosunek talii do wzrostu',high:'Wysoki stosunek talii do wzrostu'},
    half:h=>'Dla wzrostu '+fmt(h,0)+' cm połowa wzrostu to '+fmt(h/2,1)+' cm. Prosta zasada NICE: staraj się utrzymywać talię poniżej połowy wzrostu.',
    comments:{
      under:'BMI jest poniżej typowego zakresu dla dorosłych. Ten wynik nie jest sygnałem do dalszego odchudzania. Jeśli niska masa ciała nie jest zamierzona, spada lub towarzyszą jej osłabienie, utrata apetytu albo inne objawy, warto omówić to z lekarzem.',
      healthyHealthy:'BMI i stosunek talii do wzrostu są w typowych zakresach przesiewowych. To korzystny sygnał, ale nie jest pełną oceną zdrowia — znaczenie nadal mają m.in. ciśnienie, lipidy, glikemia, aktywność, palenie i wywiad rodzinny.',
      healthyCentral:'BMI jest w typowym zakresie, ale talia w stosunku do wzrostu wskazuje na zwiększone otłuszczenie centralne. To dobry przykład, dlaczego sam BMI może nie wystarczać do oceny ryzyka metabolicznego.',
      overHealthy:'BMI jest powyżej typowego zakresu, natomiast talia względem wzrostu nie wskazuje na zwiększone otłuszczenie centralne. BMI bywa zawyżone u osób z dużą masą mięśniową, dlatego warto patrzeć także na obwód talii, trend masy ciała i inne czynniki ryzyka.',
      overCentral:'BMI i talia względem wzrostu wskazują na podwyższone ryzyko kardiometaboliczne. Najbardziej praktycznym celem jest stopniowe zmniejszanie obwodu talii i poprawa czynników ryzyka, a nie gonienie za jedną „idealną” liczbą na wadze.',
      obesity:'BMI mieści się w zakresie otyłości. Warto potraktować wynik jako sygnał do pełniejszej oceny zdrowia i ryzyka kardiometabolicznego. Trwałe, stopniowe zmiany zwykle są bardziej użyteczne niż krótkie, restrykcyjne diety.',
      severe:'BMI wynosi co najmniej 35 kg/m². Przy takim BMI wskaźnik talia/wzrost wnosi mniej do przewidywania ryzyka, dlatego większe znaczenie ma całościowa ocena kliniczna, ciśnienie, glikemia, lipidy, choroby współistniejące i codzienne funkcjonowanie.'
    },
    rec:{
      maintain:'Utrzymuj regularny ruch, dietę opartą głównie na mało przetworzonych produktach, odpowiednią ilość snu i obserwuj trend talii oraz masy ciała zamiast pojedynczego pomiaru.',
      waist:'Jeśli talia wynosi co najmniej połowę wzrostu, sensownym długoterminowym celem jest jej stopniowe zmniejszanie. Unikaj gwałtownych diet i skup się na zmianach, które da się utrzymać.',
      cardio:'Przy zwiększonym otłuszczeniu centralnym warto znać swoje ciśnienie tętnicze oraz omówić z lekarzem ocenę glikemii/HbA1c i lipidogramu, szczególnie przy obciążonym wywiadzie rodzinnym lub innych czynnikach ryzyka.',
      under:'Nie stosuj deficytu kalorycznego tylko po to, by obniżyć BMI. Przy niedowadze ważniejsze są adekwatna podaż energii i białka, siła mięśniowa oraz wyjaśnienie niezamierzonego spadku masy ciała.',
      obesity:'Jeśli BMI wskazuje otyłość, rozważ rozmowę z lekarzem lub dietetykiem o realistycznym planie poprawy zdrowia. Warto oceniać także ciśnienie, glikemię i lipidy, a nie tylko masę ciała.',
      repeat:'Powtarzaj pomiary w podobnych warunkach. Talię mierz w połowie odległości między dolnym brzegiem żeber a górnym brzegiem bioder, po spokojnym wydechu.'
    },
    caution:'Kalkulator jest przeznaczony dla dorosłych i ma charakter przesiewowy. BMI nie mierzy bezpośrednio tkanki tłuszczowej; wymaga ostrożnej interpretacji m.in. przy dużej masie mięśniowej, w starszym wieku i w niektórych grupach etnicznych. W ciąży oraz u dzieci i młodzieży stosuje się inne zasady oceny.',
    bmiTitle:'BMI',whtrTitle:'Talia / wzrost',commentTitle:'Komentarz BriefRooms',recommendTitle:'Ogólne zalecenia'
  }:{
    required:'Enter valid positive values for weight, height and waist circumference.',
    range:'Check the values. The calculator supports typical adult ranges: 30–300 kg, 120–230 cm height and 35–220 cm waist circumference.',
    bmiLabel:{under:'Underweight',healthy:'Healthy range',over:'Overweight',ob1:'Obesity class I',ob2:'Obesity class II',ob3:'Obesity class III'},
    whtrLabel:{low:'Below the usual range',healthy:'Healthy central adiposity',increased:'Increased central adiposity',high:'High central adiposity'},
    half:h=>'At '+fmt(h,0)+' cm height, half your height is '+fmt(h/2,1)+' cm. The simple NICE message is to try to keep your waist below half your height.',
    comments:{
      under:'BMI is below the usual adult range. This is not a signal to lose more weight. If low body weight is unintentional, falling, or accompanied by weakness, loss of appetite or other symptoms, it is worth discussing with a clinician.',
      healthyHealthy:'Both BMI and waist-to-height ratio are within usual screening ranges. That is a favourable signal, but it is not a complete health assessment — blood pressure, lipids, glucose, activity, smoking and family history still matter.',
      healthyCentral:'BMI is within the usual range, but waist relative to height suggests increased central adiposity. This is a good example of why BMI alone can miss cardiometabolic risk.',
      overHealthy:'BMI is above the usual range, while waist-to-height ratio does not suggest increased central adiposity. BMI can overestimate adiposity in people with high muscle mass, so waist size, trends and other risk factors also matter.',
      overCentral:'Both BMI and waist-to-height ratio suggest increased cardiometabolic risk. A practical goal is gradual waist reduction and improvement in risk factors rather than chasing one “ideal” scale number.',
      obesity:'BMI is in the obesity range. Treat this as a prompt for a broader health and cardiometabolic risk assessment. Sustainable, gradual changes are generally more useful than short, restrictive diets.',
      severe:'BMI is at least 35 kg/m². At this level, waist-to-height ratio adds less predictive value, so overall clinical assessment, blood pressure, glucose, lipids, comorbidities and day-to-day function matter more.'
    },
    rec:{
      maintain:'Maintain regular physical activity, a diet based mainly on minimally processed foods, adequate sleep and follow waist and weight trends rather than a single measurement.',
      waist:'If your waist is at least half your height, a reasonable long-term aim is to reduce it gradually. Avoid crash diets and focus on changes you can sustain.',
      cardio:'With increased central adiposity, it is useful to know your blood pressure and to discuss glucose/HbA1c and lipid assessment with a clinician, especially if you have family history or other risk factors.',
      under:'Do not use a calorie deficit just to lower BMI. With underweight, adequate energy and protein intake, muscle strength and assessment of unintentional weight loss matter more.',
      obesity:'If BMI is in the obesity range, consider discussing a realistic health-improvement plan with a doctor or dietitian. Blood pressure, glucose and lipids matter in addition to body weight.',
      repeat:'Repeat measurements under similar conditions. Measure the waist midway between the lower ribs and the top of the hips, after a relaxed breath out.'
    },
    caution:'This is an adult screening tool. BMI does not directly measure body fat and should be interpreted cautiously in people with high muscle mass, in older adults and in some ethnic groups. Pregnancy, children and adolescents require different assessment methods.',
    bmiTitle:'BMI',whtrTitle:'Waist / height',commentTitle:'BriefRooms comment',recommendTitle:'General guidance'
  };

  function parse(id){return Number(String(document.getElementById(id).value||'').trim().replace(',','.'));}
  function fmt(value,digits){return Number(value).toLocaleString(lang==='pl'?'pl-PL':'en-GB',{minimumFractionDigits:digits,maximumFractionDigits:digits});}
  function bmiCategory(bmi){
    if(bmi<18.5) return 'under';
    if(bmi<25) return 'healthy';
    if(bmi<30) return 'over';
    if(bmi<35) return 'ob1';
    if(bmi<40) return 'ob2';
    return 'ob3';
  }
  function whtrCategory(r){
    if(r<0.4) return 'low';
    if(r<0.5) return 'healthy';
    if(r<0.6) return 'increased';
    return 'high';
  }
  function commentFor(bmiKey,whtrKey,bmi){
    if(bmiKey==='under') return T.comments.under;
    if(bmi>=35) return T.comments.severe;
    const central=whtrKey==='increased'||whtrKey==='high';
    if(bmiKey==='healthy') return central?T.comments.healthyCentral:T.comments.healthyHealthy;
    if(bmiKey==='over') return central?T.comments.overCentral:T.comments.overHealthy;
    return T.comments.obesity;
  }
  function recommendationsFor(bmiKey,whtrKey,bmi){
    const central=whtrKey==='increased'||whtrKey==='high';
    const list=[];
    if(bmiKey==='under'){
      list.push(T.rec.under,T.rec.repeat);
      return list;
    }
    if(bmiKey==='healthy'&&!central) list.push(T.rec.maintain);
    if(central) list.push(T.rec.waist,T.rec.cardio);
    if(bmiKey==='over'&&!central) list.push(T.rec.maintain);
    if(bmiKey==='ob1'||bmi>=35) list.push(T.rec.obesity);
    list.push(T.rec.repeat);
    return Array.from(new Set(list));
  }

  const error=document.getElementById('bmi-whtr-error');
  const results=document.getElementById('bmi-whtr-results');
  form.addEventListener('reset',function(){error.hidden=true;results.hidden=true;});
  form.addEventListener('submit',function(event){
    event.preventDefault();
    const weight=parse('weight');
    const height=parse('height');
    const waist=parse('waist');
    if(![weight,height,waist].every(v=>Number.isFinite(v)&&v>0)){
      error.textContent=T.required;error.hidden=false;results.hidden=true;return;
    }
    if(weight<30||weight>300||height<120||height>230||waist<35||waist>220){
      error.textContent=T.range;error.hidden=false;results.hidden=true;return;
    }
    const bmi=weight/Math.pow(height/100,2);
    const whtr=waist/height;
    const bmiKey=bmiCategory(bmi);
    const whtrKey=whtrCategory(whtr);
    error.hidden=true;
    document.getElementById('bmi-value').textContent=fmt(bmi,1);
    document.getElementById('bmi-category').textContent=T.bmiLabel[bmiKey];
    document.getElementById('whtr-value').textContent=fmt(whtr,2);
    document.getElementById('whtr-category').textContent=T.whtrLabel[whtrKey];
    document.getElementById('half-height-note').textContent=T.half(height);
    document.getElementById('bmi-comment-text').textContent=commentFor(bmiKey,whtrKey,bmi);
    const ul=document.getElementById('bmi-recommendation-list');
    ul.innerHTML='';
    recommendationsFor(bmiKey,whtrKey,bmi).forEach(function(text){const li=document.createElement('li');li.textContent=text;ul.appendChild(li);});
    document.getElementById('bmi-caution').textContent=T.caution;
    results.hidden=false;
    results.scrollIntoView({behavior:'smooth',block:'start'});
  });
})();
