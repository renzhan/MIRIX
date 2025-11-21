"""
测试使用训练好的 Playbook 生成邮件回复
"""
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import httpx

sys.path.insert(0, str(Path(__file__).parent))

from ace import LiteLLMClient, Generator, Playbook

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 加载环境变量
load_dotenv('.env')


async def call_workflow_extract_api(email_content: str, email_account: str = "test@example.com") -> dict:
    """调用 workflow 提取接口"""
    url = "https://aiop-dev.item.pub/pams/workflow/extract"
    payload = {
        "content": email_content,
        "email_account": email_account
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"}
            )
            response.raise_for_status()
            result = response.json()
            return result.get("workflow_result", result)
    except httpx.HTTPError as e:
        logger.error(f"workflow提取失败: {str(e)}")
        return {
            "workflow_type": "unknown",
            "referenced_workflows": [],
            "next_steps": [],
            "reasoning": f"API调用失败: {str(e)}"
        }


async def test_generation_with_playbook():
    """测试使用训练好的 Playbook 生成回复"""
    
    # 检查 API Key
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.error("未找到 OPENAI_API_KEY，请在 .env 文件中设置")
        return
    
    logger.info("=" * 80)
    logger.info("开始测试：使用训练好的 Playbook 生成邮件回复")
    logger.info("=" * 80)
    
    # 1. 加载训练好的 Playbook
    try:
        playbook = Playbook.load_from_file("trained_email_playbook_multi_turn.json")
        logger.info(f"\n[SUCCESS] 成功加载 Playbook，包含 {len(playbook._bullets)} 条策略")
        
        # 显示策略摘要
        logger.info("\n当前 Playbook 策略概览：")
        for section_name, bullet_ids in playbook._sections.items():
            logger.info(f"  分类: {section_name}")
            logger.info(f"  策略数量: {len(bullet_ids)}")
            # 显示前3条策略内容的前50个字符
            for i, bullet_id in enumerate(bullet_ids[:3]):
                bullet = playbook._bullets[bullet_id]
                content_preview = bullet.content[:50].replace("\n", " ")
                logger.info(f"    [{i+1}] {content_preview}...")
    except Exception as e:
        logger.error(f"[ERROR] 加载 Playbook 失败: {str(e)}")
        return
    
    # 2. 初始化 LLM 客户端和 Generator
    llm_client = LiteLLMClient(
        model="gpt-4o",
        temperature=0.3,
        max_tokens=2048
    )
    
    # 🔧 Monkey patch ACE的JSON解析，支持markdown包裹的JSON
    import ace.roles
    original_safe_json_loads = ace.roles._safe_json_loads
    def patched_safe_json_loads(text: str):
        """清理markdown标记后再解析JSON"""
        cleaned = text.strip()
        # 移除markdown的```json```包裹
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]  # 移除```json
        if cleaned.startswith("```"):
            cleaned = cleaned[3:]  # 移除```
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]  # 移除结尾的```
        cleaned = cleaned.strip()
        return original_safe_json_loads(cleaned)
    
    ace.roles._safe_json_loads = patched_safe_json_loads
    logger.info("✓ 已应用JSON解析补丁（支持markdown格式）")
    
    generator = Generator(llm_client)
    
    # 3. 准备测试场景
    test_scenarios = [
        {
            "name": "场景1: Uniek Unis IT集成需求讨论会议安排",
            "email": """
发件人: Ethan Simanek <ethan.simanek@unisco.com>
收件人: EDI ITEM <edi@item.com>
主题: Uniek Unis IT Call

Hi all,

A potential customer of mine wants to schedule a call with both parties IT teams to go over integration requirements and timelines. Can you please advise on what times work best for Thursday or Friday?

A rough layout is below:

Uniek receives orders from Walmart US via and EDI connection through Cleo.
Cleo validates the integrity of the file transmission, and authenticates, etc.
Build a connection from Uniek to Unis, so you can receive the order / PO lines data for fulfillment.
We will most likely need a ship confirmation passed back to our system so we can close the order with Walmart, transmit the ASN, etc. 
Build how you invoice Uniek for each order fulfilled, container unloaded, etc. and the means of sending to Uniek for payment.

Thank you
""",
            "email_account": "edi@item.com"
        },
        {
            "name": "场景2: Kehe ASN错误 - SCAC代码查询",
            "email": """
发件人: Gavin Battaglia <Gavin@mammachia.com>
收件人: bpk2.cs@unisco.com; Mark Donangelo <Mark@mammachia.com>
抄送: Alfred Garcia <alfred.garcia@item.com>; jonathan.winkler@unisco.com; mammachia.bp@unisco.com; CS <CS@mammachia.com>; edi <edi@item.com>; Sam Blankenship <Sam@mammachia.com>
主题: RE: Kehe ASN Errors [UFB-10107]

Good morning,

Following up on this. Can you help provide SCAC codes for us on this thread as well?

3348570 -
3334755 -
3333254 -
3350178 -

Thank you,
Gavin Battaglia | Supply Chain and Sales Support Specialist

---历史邮件---

From: bpk2.cs@unisco.com
Sent: Monday, November 10, 2025 10:04 AM

Hi Alfred,

carrier scac has been updated can you please provide the list of the correct SCAC codes for Mamma chia carrier so we can verify going forward.

Thanks
Best regards,
Melissa Ortiz

---

From: Alfred Garcia
Sent: Monday, November 10, 2025 5:10 PM

Hi @bpk2.cs@unisco.com,

Please confirm the correct carrier used to ship the PO#s below. Please also be advised to strictly avoid entering invalid SCAC/carriers in the orders to avoid 945/ASN issues on the customer's end.

3348570
3334755
3333254
3350178

Regards,
Alfred Garcia

---

From: Mark Donangelo
Sent: Monday, November 10, 2025 3:50 AM

Hi Alfred,

SPS has said the carrier is missing. Can you please resend the ASNs with the carrier (and any other carrier specific information like the PRO or BOL that was left off) filled out please?

Please let me know when these are resent.

Thank you,
Mark Donangelo | Sr. Director of Operations

---

From: Mark Donangelo
Sent: Sunday, November 9, 2025 4:46 PM

Hi Alfred,

We received 4 errors from SPS for the Kehe ASNs that were sent over Friday.
I'm asking SPS Support for help. We will escalate to Randi's team Wednesday if we do not hear.
Please advise if you have any idea what the error may be so we can potentially re-upload in advance.

Thank you,
Mark Donangelo
""",
            "email_account": "alfred.garcia@item.com"
        },
        {
            "name": "场景3: API查询问题排查 - 请求credentials复制问题",
            "email": """
发件人: Evan Jackson <Evan.Jackson@colavita.com>
收件人: Michael Jan Francisco <michael.francisco@item.com>; Jennifer Kuo - Unisco <jennifer.kuo@unisco.com>
抄送: Implementation@item.com
主题: RE: Shipped orders missing from /edi/outbound/order/dc/search-by-paging

Hi,

Would you mind sending your credentials so that I can replicate the requests?

No, still no results.
Using the request body you sent below.

Thanks,
Evan

---历史邮件---

From: Michael Jan Francisco
Sent: Tuesday, November 11, 2025 9:52 AM

@Evan Jackson
I wasn't able to try it myself without your credentials. Could you please try again?

Request body:
{
    "CompanyID": "LT",
    "CustomerID": "COLUSA0001",
    "FacilityID": "825",
    "PONo": "2546TI853497",
    "ReferenceNo": "",
    "CreatedWhenFrom": "2025-10-15T00:11:11.892",
    "CreatedWhenTo": "2025-11-20T00:12:11.892",
    "Paging":{
        "PageNo":1
    }
}

---

From: Jennifer Kuo
Sent: Tuesday, November 4, 2025 4:59 PM

@Michael Jan Francisco Would you please assist with Evan's issue below? 
Both orders are showing shipped but he is not able to retrieve DC info via API.

Cesanek
DN-1183567
DN-1183566

---

From: Evan Jackson
Sent: Tuesday, November 4, 2025 7:24 AM

Hi,

Sorry have another issue/question.

We have two orders that have shipped from UNIS, but we are not seeing the corresponding DCs in the /edi/outbound/order/dc/search-by-paging URI.

The orders are SO107800 (2546NJ853495) and SO107802 (2546TI853497)

For example, if I send this as a request:
{
  "FacilityID": "825",
  "CustomerID": "COLUSA0001",
  "CompanyID": "LT",
  "PONo" : "2546TI853497"
}

I just get this back, but no data for the actual shipment:
{
  "Orders": [],
  "paging": {
    "pageNo": 1,
    "limit": 10,
    "totalCount": 4,
    "totalPage": 1,
    "startIndex": 1,
    "endIndex": 10
  }
}

Thanks,
Evan
""",
            "email_account": "michael.francisco@item.com"
        },
        {
            "name": "场景4: Amazon DF订单 - 要求提供装载照片",
            "email": """
发件人: Edgar Lopez <edgar@intertexap.com>
收件人: Jonathan (PoolerUFCS@unisco.com)
抄送: alfred.garcia@item.com; andry.lara@unisco.com; edi@unisco.com; Wilfe Obnimaga <wilfe@intertexap.com>; 等多人
主题: RE: No DN Yet_UNIS-Amazon DF Orders 11.10.25 [UFPS-4059]

Jonathan,

Be sure to send the image of the loaded packages to the UPS trailer. See attached for reference.

Best regards,
Edgar Lopez
Manager – Import & Logistics
International Textile & Apparel, Inc.
Cell: 1 213-725 4870
Email: edgar@intertexap.com

---历史邮件---

From: PoolerUFCS@unisco.com
Sent: Tuesday, November 11, 2025 5:53 AM

Hello Wilfe,
ADF DN-437300 order was loaded onto trailer 307759

Thank you,
Jonathan Divino
UNIS Fulfillment Client Support
Mobile: 16266262267

---

From: UNIS Fulfillment - Seabrook
Sent: Monday, November 10, 2025 5:20 PM

Hello Wilfe,
DN-437300 is received.

Thank you,
Jonathan Divino

---

From: wilfe@intertexap.com
Sent: Monday, November 10, 2025 5:13 PM

Hi Jonathan,
DN is now available
Labels uploaded
Thank you.

Kindest regards,
Wilfe Obnimaga

---

From: wilfe@intertexap.com
Sent: Monday, November 10, 2025 4:58 PM

Good Morning Jonathan,

Total Orders: 117
Amazon Direct Fulfillment orders for today.
Please confirm once received.
All orders are in units
ORDERS SHOULD GO OUT AND SHIP SAME DAY.

79259/79259A – IN STOCK please ship 1 full case 78966 (78966 x 6units)
79637 – IN STOCK ship 2 units 79165/label
79078C – please ship 1full case of 79078 / label
79185 – tape 2 units 79184
""",
            "email_account": "jonathan.divino@unisco.com"
        },
        {
            "name": "场景5: Walmart短缺索赔 - BOL数量单位显示问题",
            "email": """
发件人: Jason Stephen <jstephen@honeystinger.com>
收件人: Katie McDonnell <katie.mcdonnell@unisco.com>; Jennifer Kuo <jennifer.kuo@unisco.com>
抄送: Alfred Garcia <alfred.garcia@item.com>; implementation item <implementation@item.com>; 等多人
主题: RE: Walmart shortage claims

Hi Jason,

Our IT team is having this escalated, this was the latest update I received.
Thanks!

---历史邮件---

From: Jennifer Kuo
Sent: Tuesday, November 11, 2025 8:18 PM

Still no response so I've escalated.

Jennifer Kuo
Account Implementation Manager | IT - Implementation

---

From: Katie McDonnell
Sent: Tuesday, November 11, 2025 7:16 AM

Hi Jennifer,
Can we get an update on this ticket please?

Thanks
Katie McDonnell
Strategic Account Manager

---

From: Jennifer Kuo
Sent: Monday, November 3, 2025 10:25 AM

Hello Katie:
No update at this time. I have followed up again just now with the dev team.
Thank you for your patience.

---

From: Jennifer Kuo
Sent: Thursday, October 30, 2025 3:13 PM

Hi Nick:
This is not something that can be addressed by EDI – it's a BOL template issue.

I don't know if the UOM conversion can be done on the BOL template as, unlike packing lists and labels, there is just one version shared by all customers. Which means, any change we make to the template will affect ALL accounts.

Submitted a ticket (IMPLE-218) and will advise.

---

From: Nick Blake
Sent: Thursday, October 30, 2025 12:51 PM

It seems the deal as we generate our producing units and inners as that is how the orders come into Wise but for transportation purposes ideally we need this to be cases.

---

From: Jason Stephen
Sent: Thursday, October 30, 2025 11:50 AM

@Katie McDonnell - it looks like part of the issue is that the BOL you're using is showing the number of inner cartons, not how many master cases (shipping cartons). See below. 

This order was only for 26 cartons but your BOL says 104 cases.

Jason Stephen
Operations Manager, Honey Stinger

---

From: Rob Mckenna
Sent: Tuesday, October 28, 2025 3:24 PM

Good afternoon,
I have 3 orders that shipped thru the Walmart consolidation that they are claiming short. 
Do we have signed PODs for these orders to fight the claim?

Thanks!
Rob McKenna
Senior Distribution Coordinator, Honey Stinger
""",
            "email_account": "jennifer.kuo@item.com"
        },
        {
            "name": "场景6: Hint Go-Live确认 - 上线时间协调",
            "email": """
发件人: Joe Geraldi <joe.geraldi@unisco.com>
收件人: Shelia Sun <shelia.sun@item.com>; Jennifer Kuo - Unisco <jennifer.kuo@unisco.com>; Jinhao Zhang <jinhao.zhang@item.com>; edi <edi@item.com>
主题: Re: Hint Go-Live

If it's not doable 100% let me know but I just would like to understand why we need that long to move into production.

Joe Geraldi | unis
Vice President of Sales
www.unisco.com
m: 847.528.1368

---历史邮件---

From: Joe Geraldi
Sent: Tuesday, November 11, 2025 7:14 PM

We need Monday. If I can get them to confirm tomorrow can we go into production Monday the 17th? Help me understand why we would need that long?

---

From: Shelia Sun
Sent: Tuesday, November 11, 2025 7:09 PM

Hi Joe，
The 943 and 944 transactions are already live, while the 940 and 945 are still under adjustment with the customer.

Once the customer confirms that all test cases have been completed, we can commit to going live with the outbound part next Thursday, China time.

Best Regards
Shelia Sun
Sr. EDI / Implementation Manager
shelia.sun@item.com

---

From: Joe Geraldi
Sent: Tuesday, November 11, 2025 1:04 PM

Hi guys,
Hint has been implementing for a while now. We need to go-live Monday as their current 3pl is kicking them out. can we make this happen for the outbound? @EDI ITEM

Joe Geraldi | unis
Vice President of Sales
www.unisco.com
m: 847.528.1368
""",
            "email_account": "shelia.sun@item.com"
        },
        {
            "name": "场景7: Phillips Healthcare客户代码查询 - 账户映射问题",
            "email": """
发件人: Luke Darang <luke.darang@unisco.com>
收件人: Corey Wheeler <corey.wheeler@unisco.com>
抄送: billingteam@unisco.com; Wise Support <WISEsupport@unisco.com>; Luis Carreiro <luis.carreiro@unisco.com>
主题: RE: Somerset Facility-C.H.Robinson-Philips Healthcare

Corey,

May you help to provide the customer code? Currently we have 3 Phillips Healthcare accounts. Thank you.

Luke Darang
UF Billing/Setup Supervisor
Phone: 630.538.3280
Facility: 218 Machlin Ct, Walnut, CA, 91789
Email: luke.darang@unisco.com

---历史邮件---

From: Corey Wheeler
Sent: Friday, November 7, 2025 8:28 AM

Hey @billingteam@unisco.com,

Can you add customer-C.H.Robinson-Philips Healthcare in Somerset Facility. It was in New Jersey facility and wasn't mapped over to the new facility.

Corey Wheeler
Senior IT WMS Support Specialist
Cell: (626) 346-4571
corey.wheeler@item.com | www.item.com
""",
            "email_account": "corey.wheeler@unisco.com"
        },
        {
            "name": "场景8: 新客户上线流程协调 - 2.0 vs 3.0平台选择",
            "email": """
发件人: Veronica Eve Serra <veronica.serra@item.com>
收件人: Shelia Sun <shelia.sun@item.com>; Stacy Nguyen <stacy.nguyen@item.com>; Jennifer Kuo <jennifer.kuo@item.com>
抄送: Frances Parro Belleza <frances.belleza@item.com>
主题: Re: New Customers - Onboard to 2.0 vs. 3.0

Hi Shelia,

Thanks for this.

I'll review this and will coordinate with you on other items that needs assistance.

Appreciate the fast revert on this 🙂

Thanks,
Veronica Eve Dae "Vee" Serra
Sr. Scrum Master
Phone: +63917-179-0119
Email: veronica.serra@item.com

---历史邮件---

From: Shelia Sun
Sent: Thursday, November 6, 2025 11:12 AM

Hi Vee,
I have a list that includes the customers currently in progress please refer to the attachment, As of 10/11

Best Regards
Shelia Sun
Sr. EDI / Implementation Manager
shelia.sun@item.com

---

From: Veronica Eve Serra
Sent: Friday, November 7, 2025 12:05 AM

Hi Shelia,
I was able to review the list you have provided.
Do you also have a list of the customer pipeline so that we can plan out on which facility and platform they are to be onboarded to?

Thanks,
Vee Serra

---

From: Veronica Eve Serra
Sent: Wednesday, November 5, 2025 10:25 AM

Hi Shelia,
Can we have the list of onboarded customers for the year?
Since we started migrating customers to 3.0 during the start of the year, it will be worthwhile to cross reference those customers vs facilities that are onboarded as well.
Noted on those customers that will not be included in the list.

Appreciate your help on this Shelia. 🙂

---

From: Stacy Nguyen
Sent: Tuesday, November 4, 2025 10:13 AM

Hi Jen, Shelia

As we transition all facilities from 2.0 to 3.0, there are still some specific customers or facilities that may need to remain on 2.0 this year. When the Implementation team onboards any new customer, please include both Vee and Frances in the loop from the beginning.

Vee will update the customer vs. facility vs. 2.0/3.0 master file.
WISE/Frances will advise Implementation whether the customer should be on 2.0 or 3.0 based on the facility.

This process ensures that customers are not onboarded to 2.0 if their facility is already running on 3.0.

Stacy Nguyen
IT General Manager
stacy.nguyen@item.com
""",
            "email_account": "shelia.sun@item.com"
        },
        {
            "name": "场景9: Sterling Implementation会议邀请确认 - 添加参会人",
            "email": """
发件人: Nicholas Ali <nicholas@sterlingrivers.com>
收件人: Jennifer Kuo <jennifer.kuo@unisco.com>
抄送: Scott Simanek <scott.simanek@unisco.com>; Mia Chen <mia.chen@item.com>; implementation item <implementation@item.com>; 等多人
主题: Re: Unis-item / Sterling Implementation Touchbase

Hi Jennifer,

This time works. Please add Serene to the invite.

Nick

---历史邮件---

From: Jennifer Kuo
Sent: Wednesday, November 5, 2025 5:50 PM

Hello Nicholas:

Will you be available for a touchbase meeting tomorrow at 7am PST?

I am sending out the invite but please let me know if time needs to be adjusted. I'm setting at this earlier time so that our overseas dev team can join in.

Thank you,
Jennifer Kuo
Account Implementation Manager | IT - Implementation
mobile: 657 – 705 - 7549
jennifer.kuo@item.com | jennifer.kuo@unisco.com
""",
            "email_account": "jennifer.kuo@unisco.com"
        },
        {
            "name": "场景10: TCL发票标签错误 - B2B vs B2C分类问题",
            "email": """
发件人: Marielle Badua <marielle.badua@tcl.com>
收件人: billingteam@unisco.com; Jin Zhang <jin.zhang@unisco.com>; Paul Darang <paul.darang@unisco.com>; Tom Yu <tom.yu@unisco.com>; Alan Yang <alan.yang@unisco.com>
抄送: UF Invoice <UFinvoice@unisco.com>; Arthur Navarrete <arthur.navarrete@tcl.com>; Ruoyi Jin <ruoyi.jin@tcl.com>; Zac Zalewski <zac.zalewski@tcl.com>
主题: Re: [External Sender]19279840 - TTE TECHNOLOGY INC DBA TCL NORTH AMERICA - billing period (10/26/2025-11/01/2025) - service type (OUTBOUND-COMPUTER MONITORS) - 109-Riverside,DocumentDate(11/05/2025)

@billingteam@unisco.com@Jin Zhang@Paul Darang@Tom Yu@Alan Yang

Unis team,

Please note DROPSHIP is not B2B, it is B2C.

This invoice and a handful of others are inaccurately labelled.

Order Processing - B2B = Regular Order
Order Processing - B2C = Dropship

And rates are assigned as such. Please work to increase accuracy of charge labels.

Best,
Marielle Badua | Supply Chain Financial Specialist
TCL North America
e: Marielle.Badua@tcl.com

---历史邮件---

From: UFinvoice
Sent: Wednesday, November 5, 2025 2:18 PM

Dear Customer,

Kindly find attached invoices and backups for the activities at '109-Riverside'.

Should you have any question, please feel free to contact us. Thank you.
""",
            "email_account": "paul.darang@unisco.com"
        },
        {
            "name": "场景11: ZURU发票分类会议确认 - GL代码和发票分类讨论",
            "email": """
发件人: Kate Pang <kate.pang@zuru.com>
收件人: Mari Mendoza <mari.mendoza@unisco.com>; Luke Darang <luke.darang@unisco.com>; Mona Yao <Mona.yao@zuru.com>
抄送: Vlad Plyusnin <Vlad@zuru.com>; LOP US <lop-US@zuru.com>; Ayman Aslam <Ayman@zuru.com>; Implementation <implementation@unisco.com>; billingteam@unisco.com; Sam Warheit <sam.warheit@unisco.com>
主题: Re: Invoice Classification, GL Codes, and Rationale

Hi Mari

OK， slot is ok for us, Pls send through the invitation. Thanks!

---历史邮件---

From: Mari Mendoza
Sent: Monday, November 10, 2025 10:22 AM

HI Kate,
Thank you and we will be awaiting your response.

Mari Mendoza
Implementation | Operations Support Specialist
Mobile: 909.551.8354

---

From: Kate Pang
Sent: Wednesday, November 5, 2025 9:36 PM

Hi Mari
Ayman will be back from holiday, let me double check his availability first, I will get back to you on Nov 10th. Thanks!

---

From: Mari Mendoza
Sent: Thursday, November 6, 2025 11:15 AM

Would 5pm on 11/12 work for the team?

---

From: Luke Darang
Sent: Monday, November 3, 2025 8:22 AM

Hi Kate,
Sure thing, any day should work for me just let us know what day will be ideal for the team. Thank you!

---

From: Kate Pang
Sent: Monday, November 3, 2025 12:18 AM

Thanks Luke
I'm just realizing this week to be quite overwhelming. Could we possibly aim for the week of November 10th to November 14th instead? Ayman will have returned by then, so we could arrange to have him involved in our discussions. 5 PM PST would be more manageable for our team. Ideally, we would look for a slot between Monday and Friday of that week. Thank you!

---

From: Mari Mendoza
Sent: Thursday, October 30, 2025 12:24 AM

HI @Mona.yao@zuru.com & @kate.pang@zuru.com,
To better align on the attached and expected invoice requirements. Let's please put a meeting on the calendar to review.
Please share dates/times of availability for this week and next week.

---

From: Mona Yao
Sent: Wednesday, October 15, 2025 10:21 PM

Hi @Luke Darang,
Please find the attached file for your reference.
We have highlighted the incorrect GL codes in Column C using yellow, and provided the corresponding corrected GL codes in Column D. If you have any questions or need further clarification, please feel free to reach out.

---

From: Luke Darang
Sent: Thursday, October 16, 2025 12:59 AM

Hi Zuru Team,
Attached are the current charge lines setup, kindly help confirm if these are tagged accordingly. Thank you.

---

From: Kate Pang
Sent: Monday, October 13, 2025 9:35 AM

Hi Sam
It was a pleasure e-meeting with you. My name is Kate, and I am a colleague of Ayman's at ZURU.

Please find enclosed ZURU's Standard Classification and the Brief Rationale for the invoice categories and corresponding GL codes.

Generally, our operational costs are divided into three main areas:
Inbound: Our objective is to analyze inbound order costs down to the unit level. Therefore, we must include all invoice costs associated with inbound activities. This encompasses charges for unloading, palletization, additional labor, documentation, and any other associated fees that related to inbound.

Stock: This section covers fees related to storage and general warehouse management. These costs need to be split so we can provide individual storage costs per pallet for each market, alongside overall warehouse cost statistics.

Outbound: We strive to analyze outbound order costs down to the unit level. This section includes invoice costs and fees associated with outbound orders, such as picking, packing, labeling, and transportation.
""",
            "email_account": "mari.mendoza@unisco.com"
        },
        {
            "name": "场景12: Location地址更新确认 - Paylocity系统更新完成",
            "email": """
发件人: Dana Cardwell <dana.cardwell@unisco.com>
收件人: Jason Lu <jason.lu@unisco.com>; Payroll <payroll@unisco.com>; 等多人
抄送: Samwise Luo <samwise.luo@unisco.com>
主题: Re: Location List 11072025 - Address Change for Location #146

Ok I corrected in Paylocity under Location and Work Location

Dana Cardwell
Payroll Manager
Payroll Department
Cell: 626-420-7730
dana.cardwell@unisco.com | www.unisco.com

---历史邮件---

From: Jason Lu
Sent: Friday, November 7, 2025 4:48 PM

Yes, that's correct. It's the same location; only the address has been updated.

Thank you!

Jason Lu
Financial Analyst
Office 909.839.0201
jason.lu@unisco.com|www.unisco.com

---

From: Payroll (Dana Cardwell)
Sent: Friday, November 7, 2025 4:36 PM

Just to be clear it's the same location Id 146 – just update address to 12104. Its not a whole new location?

Dana Cardwell
Payroll Manager
Payroll Department
Cell: 626-420-7730
dana.cardwell@unisco.com | www.unisco.com

---

From: Jason Lu
Sent: Friday, November 7, 2025 11:04 AM

Hello all,

Please note that the address for Location #146 has been changed from:

12100 Emerald Pass Drive, Building #4, El Paso, TX 79928

to:

12104 Emerald Pass Drive, Building #4, El Paso, TX 79928

Kindly update your records accordingly.

Let me know if you have any questions.

Thank you!

Jason Lu
Senior Financial Analyst
Office 909.839.0201
jason.lu@unisco.com|www.unisco.com
""",
            "email_account": "jason.lu@unisco.com"
        },
        {
            "name": "场景13: 新员工系统访问权限设置 - Data Entry员工工具访问请求",
            "email": """
发件人: Tweetie Leigh Lamoste <tweetie.lamoste@unisco.com>
收件人: helpdesk <helpdesk@unisco.com>; Mary Smothers <mary.smothers@unisco.com>
抄送: Janera Mahilwas <janera.mahilwas@unisco.com>; Heinz Morillo <heinz.morillo@unisco.com>; Patrick Paul Pogoy <patrick.pogoy@unisco.com>
主题: RE: Newbie Access | Data Entry: Leah Mae Zafra [ITS-6205]

Thank you, Mary!

Hi @helpdesk,

Please assist at your earliest convenience so our new hire can start using our tools.

++ @Wise Support as well for additional assistance on the below tools.

Thank you so much all!

Kind regards,
Tweetie Leigh Lamoste
UNIS Fulfillment Client Support Team Lead
Phone: 626.271.9858
Email: tweetie.lamoste@unisco.com
Facility: 175 Cesanek Road, Northampton, PA 18067

---历史邮件---

From: Mary Smothers
Sent: Tuesday, November 11, 2025 9:28 AM

Approved

Thank you,
Mary Smothers
Corporate Accounts Manager/Davao Liaison
Mobile: 626.899.2363
Email: mary.smothers@unisco.com

---

From: Tweetie Leigh Lamoste
Sent: Tuesday, November 11, 2025 9:17 AM

Hi @Mary,

We kindly ask for your approval for access on the below tools that our new employee needs.

Name: Leah Mae Zafra
Department: Data Entry
Employee code: 10883
PC Number: SBN-PH282

* 3CX
* MS Teams
* Outlook (if still needed since she's from DE)
* UNIS Ticketing System (future reference, if possible)
* Helpdesk
* JIRA
* Paylocity

Thank you,
Tweetie Leigh Lamoste
UNIS Fulfillment Client Support Team Lead

---

From: helpdesk
Sent: Tuesday, November 11, 2025 8:29 AM

Hi Tweetie

Ticket # ITS-6205 created.

Kindly review the form below. If any required information is missing, please respond to this email with the necessary details as indicated in the form.

Request-Account
Please attache your supervisor approval

If your concern is urgent, please contact:
UNIS IT Support - +1 626-626-9896
WISE/TMS Support - +1 626-626-2299
""",
            "email_account": "helpdesk@unisco.com"
        },
        {
            "name": "场景14: 招聘流程 - 候选人offer批准请求",
            "email": """
发件人: Joseph Sparacino <joseph.sparacino@unisco.com>
收件人: Stacy Nguyen <stacy.nguyen@unisco.com>; Shelia Sun <shelia.sun@item.com>; Juan Cervantes <juan.cervantes@unisco.com>; Jimoh Yusuf <jimoh.yusuf@unisco.com>; Jennifer Kuo <jennifer.kuo@unisco.com>
抄送: Jennifer Kuo <jennifer.kuo@item.com>; Sandra Ramirez <sandra.ramirez@unisco.com>
主题: RE: Requisition Form - Vinay Katnam - IT Implementation Specialist - Buena Park

Hi All,

Vinay is not getting back to me.

@Stacy Nguyen Please approve $75k offer to Manasa as backup to Vinay.

Goutham just notified me that he will be accepting the $75k offer and no relocation assistance required.

Thank You.

Joseph Sparacino
Technical Recruiter
O 909.551.8337
M 949.633.1063
Joseph.sparacino@unisco.com
www.unisco.com

---历史邮件---

From: Shelia Sun
Sent: Monday, November 10, 2025 4:35 PM

Hi Joseph,
Yes, if we are not able to reach Vinay, then let's proceed with sending the offer to the other candidate. Thank you.

Best Regards
Shelia Sun
Sr. EDI / Implementation Manager
shelia.sun@item.com

---

From: Joseph Sparacino
Sent: Tuesday, November 11, 2025 8:28 AM

Hi Shelia,
Vinay is not getting back to me.
Did you want to offer Manasa instead?

Joseph Sparacino
Technical Recruiter

---

From: Shelia Sun
Sent: Monday, November 10, 2025 4:21 PM

Hi Joseph，
Could you please confirm whether our latest candidate, Goutham Lokku, has accepted the offer and committed to a start date?

If Vinay decides to decline the offer, could you please reach out to Manasa Pittala as soon as possible?

Also, I've noticed that many of the recent candidates are from India — please help provide us with a bit more variety in profiles going forward, haha. Thank you!

Best Regards
Shelia Sun
Sr. EDI / Implementation Manager

---

From: Stacy Nguyen
Sent: Monday, November 10, 2025 8:44 AM

Hello. Juan, Joseph
Sandra just informed me that Vinay didn't show up for the orientation today. Has he completed the new hire application and been informed that his first day is today?

Stacy Nguyen
IT Office Manager
M 626.803.6525
stacy.nguyen@unisco.com| www.unisco.com

---

From: Joseph Sparacino
Sent: Monday, November 10, 2025 1:06 PM

Hi Stacy,
He knew today was his first day. I have msgs out to him.

Joseph Sparacino
Technical Recruiter

---

From: Juan Cervantes
Sent: Monday, November 3, 2025 11:09 AM

Thanks, Joe.
The offer letter has been sent.

Juan Cervantes
Regional HR Manager
www.unisco.com
626.803.6528
""",
            "email_account": "stacy.nguyen@unisco.com"
        }
    ]
    
    # 4. 对每个场景生成回复
    for scenario in test_scenarios:
        logger.info("\n" + "=" * 80)
        logger.info(f"\n{scenario['name']}")
        logger.info("=" * 80)
        logger.info(f"\n收到的邮件：\n{scenario['email']}")
        
        # 构造上下文（只包含原始邮件）
        context = json.dumps({
            "original_email": scenario['email'],
            "history": ""
        }, ensure_ascii=False)
        
        # 使用 Generator 生成回复
        logger.info("\n[STEP] 使用 Generator + Playbook 生成回复...")
        try:
            result = generator.generate(
                playbook=playbook,
                question="如何处理回复这封邮件？需要具体执行哪些步骤？需要联系哪些人和团队？",
                context=context
            )
            
            logger.info("\n" + "🤖 " * 40)
            logger.info("生成的邮件回复：")
            logger.info("=" * 80)
            logger.info(result.final_answer)
            logger.info("=" * 80)
            
            # 显示使用的策略（如果有该属性）
            if hasattr(result, 'bullets_used') and result.bullets_used:
                logger.info(f"\n✓ 使用了 {len(result.bullets_used)} 条 Playbook 策略：")
                for bullet_id in result.bullets_used:
                    if bullet_id in playbook._bullets:
                        bullet = playbook._bullets[bullet_id]
                        content_preview = bullet.content[:80].replace("\n", " ")
                        logger.info(f"  • [{bullet_id}] {content_preview}...")
            else:
                # 这是正常现象，ACE Generator 内部使用策略但不返回具体的策略ID列表
                logger.debug("[DEBUG] 当前ACE版本不返回具体策略ID（这不影响生成质量）")
                
        except Exception as e:
            logger.error(f"\n[ERROR] 生成回复失败: {str(e)}")
            import traceback
            traceback.print_exc()
    
    logger.info("\n" + "=" * 80)
    logger.info("测试完成！")
    logger.info("=" * 80)


if __name__ == "__main__":
    asyncio.run(test_generation_with_playbook())

