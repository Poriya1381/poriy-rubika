const botUrl = "https://rubika.ir/PoriyBot";

const tabs = document.querySelectorAll(".tab");
const panels = {
  members: document.getElementById("panel-members"),
  rubino: document.getElementById("panel-rubino"),
  other: document.getElementById("panel-other")
};

tabs.forEach(tab => {
  tab.addEventListener("click", () => {
    tabs.forEach(t => t.classList.remove("active"));
    tab.classList.add("active");
    Object.values(panels).forEach(p => p.classList.remove("active"));
    panels[tab.dataset.tab].classList.add("active");
  });
});

function openService(type){
  const map = {members:"members", rubino:"rubino", views:"other", reactions:"other"};
  const tab = document.querySelector(`.tab[data-tab="${map[type]}"]`);
  if(tab) tab.click();
  document.getElementById("pricing").scrollIntoView({behavior:"smooth", block:"start"});
}

let currentOrder = null;

function order(service, plan, price){
  currentOrder = {service, plan, price};
  document.getElementById("orderService").textContent = service;
  document.getElementById("orderPlan").textContent = plan;
  document.getElementById("orderPrice").textContent = `${price} تومان`;
  document.getElementById("modalTitle").textContent = "ثبت سفارش";
  document.getElementById("modalInfo").textContent = "اطلاعات سفارش را بررسی کن و برای ادامه وارد ربات شو.";
  document.getElementById("targetInput").value = "";
  const modal = document.getElementById("orderModal");
  modal.classList.add("show");
  modal.setAttribute("aria-hidden","false");
  document.body.style.overflow = "hidden";
}

function closeModal(){
  const modal = document.getElementById("orderModal");
  modal.classList.remove("show");
  modal.setAttribute("aria-hidden","true");
  document.body.style.overflow = "";
}

function sendToBot(){
  if(!currentOrder) return;
  const target = document.getElementById("targetInput").value.trim();
  const text =
`سلام، برای ثبت سفارش از سایت Poriy پیام دادم.
خدمت: ${currentOrder.service}
پلن: ${currentOrder.plan}
مبلغ: ${currentOrder.price} تومان` +
(target ? `\nمقصد: ${target}` : "");
  // Rubika does not provide a guaranteed public URL scheme for prefilled bot messages,
  // so copy the order text and open the bot profile.
  navigator.clipboard?.writeText(text).catch(()=>{});
  window.open(botUrl, "_blank", "noopener");
  document.getElementById("modalInfo").textContent =
    "متن سفارش کپی شد. داخل ربات ارسالش کن تا سفارش ادامه پیدا کند.";
}

document.addEventListener("keydown", e => {
  if(e.key === "Escape") closeModal();
});

document.getElementById("year").textContent = new Date().getFullYear();
