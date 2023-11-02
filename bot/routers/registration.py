from loguru import logger
from vkwave.bots import BotEvent, DefaultRouter, Keyboard, SimpleBotEvent
from vkwave.bots.fsm import NO_STATE, ForWhat, StateFilter

from bot import aliases, messages, parsers, settings, vk_util
from bot.database.models import Donor, Municipality
from bot.fsm import FSM, FSMDataKey, HomeState, RegistrationState
from bot.keyboards import ChooseMunicipalityKeyboard, OrgOrSelfChoiceKeyboard
from bot.routers import home

FOR_USER = ForWhat.FOR_USER

router = DefaultRouter()
reg = router.registrar

MAX_ORGANIZATION_NAME_LEN = 500  # max length of organization name


@reg.with_decorator(StateFilter(fsm=FSM, state=NO_STATE, for_what=FOR_USER))
async def start(event: BotEvent):
    event = SimpleBotEvent(event)
    user_id = event.from_id

    donor = await Donor.find_one(Donor.user_id == user_id)
    if not donor:
        first_name, last_name = await vk_util.fetch_current_user_name(event)
        await event.answer(
            f"👋 Привет, {first_name}! Я бот для проведения акции <<Коробочка доброты>>.\n\n"
            "С моей помощью ты сможешь выбрать одного или нескольких человек с "
            "ограниченными возможностями, которым будешь дарить подарок.\n\n"
            f"Обрати внимание, что выбранные подарки будет необходимо принести на точку сбора до {settings.DEADLINE}.\n\n"
            "Но, для начала, мне нужно узнать некоторую информацию о тебе."
        )
        await FSM.set_state(state=RegistrationState.SET_MUNICIPALITY, event=event, for_what=FOR_USER)
        await FSM.add_data(event, for_what=FOR_USER, state_data={FSMDataKey.DONOR_NAME: f"{first_name} {last_name}"})

        logger.info(f"a new user {first_name} {last_name} (vk.com/id{user_id}) started registration")
        await request_municipality(event)
    else:
        logger.warning(f"existing donor {donor} without state has started registration, sending him home")
        await home.send_home(event)


async def request_municipality(event: BotEvent):
    event = SimpleBotEvent(event)

    municipalities = await Municipality.find_all().to_list()
    kbd = ChooseMunicipalityKeyboard([municipality.name for municipality in municipalities])
    await event.answer(
        "Выбери населенный пункт, в котором ты будешь принимать участие в акции:",
        keyboard=kbd.get_keyboard(),
    )


@reg.with_decorator(StateFilter(fsm=FSM, state=RegistrationState.SET_MUNICIPALITY, for_what=FOR_USER))
async def set_city(event: BotEvent):
    event = SimpleBotEvent(event)
    municipality_name = event.text

    if not await Municipality.find_one(Municipality.name == municipality_name):  # if municipality not found
        await event.answer("Этот город не участвует в акции! Пожалуйста, выбери название города с помощью клавиатуры.")
        return

    kbd = OrgOrSelfChoiceKeyboard()
    await event.answer(
        "Ты будешь участвовать в акции от своего имени или от имени организации?",
        keyboard=kbd.get_keyboard(),
    )
    await FSM.add_data(event, for_what=FOR_USER, state_data={FSMDataKey.MUNICIPALITY_NAME: municipality_name})
    await FSM.set_state(state=RegistrationState.CHOOSE_SELF_OR_ORG, event=event, for_what=FOR_USER)


@reg.with_decorator(StateFilter(fsm=FSM, state=RegistrationState.CHOOSE_SELF_OR_ORG, for_what=FOR_USER))
async def choose_self_or_org(event: BotEvent):
    event = SimpleBotEvent(event)

    match event.text:
        case OrgOrSelfChoiceKeyboard.SELF:  # no organization
            await request_phone_number(event)
            await FSM.set_state(state=RegistrationState.SET_PHONE_NUMBER, event=event, for_what=FOR_USER)
            await FSM.add_data(event=event, for_what=FOR_USER, state_data={FSMDataKey.ORGANIZATION_NAME: None})

        case OrgOrSelfChoiceKeyboard.ORG:  # organization
            kbd = Keyboard()
            await event.answer(
                "Хорошо. Тогда напиши, пожалуйста, название этой организации.",
                keyboard=kbd.get_empty_keyboard(),
            )
            await FSM.set_state(state=RegistrationState.SET_ORGANIZATION_NAME, event=event, for_what=FOR_USER)

        case _:  # other choice
            await event.answer(messages.INVALID_OPTION)


@reg.with_decorator(StateFilter(fsm=FSM, state=RegistrationState.SET_ORGANIZATION_NAME, for_what=FOR_USER))
async def set_organization(event: BotEvent):
    event = SimpleBotEvent(event)
    organization_name = event.text

    if len(organization_name) > MAX_ORGANIZATION_NAME_LEN:
        return await event.answer(f"Слишком длинное название организации. {messages.TRY_AGAIN}")

    await request_phone_number(event)
    await FSM.set_state(state=RegistrationState.SET_PHONE_NUMBER, event=event, for_what=FOR_USER)
    await FSM.add_data(event=event, for_what=FOR_USER, state_data={FSMDataKey.ORGANIZATION_NAME: organization_name})


async def request_phone_number(event: BotEvent):
    event = SimpleBotEvent(event)
    kbd = Keyboard()
    await event.answer(
        "Отлично! Теперь, пожалуйста, отправь номер телефона по которому, "
        "в случае чего, волонтеры смогли бы с тобой связаться.",
        keyboard=kbd.get_empty_keyboard(),
    )


@reg.with_decorator(StateFilter(fsm=FSM, state=RegistrationState.SET_PHONE_NUMBER, for_what=FOR_USER))
async def set_phone_number(event: BotEvent):
    event = SimpleBotEvent(event)
    try:
        phone_number = parsers.parse_phone_number(event.text)
    except parsers.ParsingError:
        await event.answer(f"Эээ... Это что-то не очень похоже на номер телефона. {messages.TRY_AGAIN}")
        return

    await FSM.add_data(event=event, for_what=FOR_USER, state_data={FSMDataKey.PHONE_NUMBER: phone_number})
    await request_registration_confirmation(event)


async def request_registration_confirmation(event: BotEvent):
    event = SimpleBotEvent(event)

    fsm_data = await FSM.get_data(event=event, for_what=FOR_USER)

    municipality_name = fsm_data[FSMDataKey.MUNICIPALITY_NAME]
    organization_name = fsm_data[FSMDataKey.ORGANIZATION_NAME]
    phone_number = fsm_data[FSMDataKey.PHONE_NUMBER]

    response = "Давай проверим данные:\n" f"- Населенный пункт: {municipality_name}\n"
    if organization_name:
        response += f"- Организация: <<{organization_name}>>\n"
    else:
        response += "- Участвуешь в акции не от имени организации\n"

    response += f"- Контактный номер телефона: {phone_number}\n\n" f"Все верно?"

    await aliases.send_confirmation(
        event=event,
        text=response,
        confirmation_handler_state=RegistrationState.CONFIRM_REGISTRATION,
    )


@reg.with_decorator(StateFilter(fsm=FSM, state=RegistrationState.CONFIRM_REGISTRATION, for_what=FOR_USER))
async def confirm_registration(event: BotEvent):
    event = SimpleBotEvent(event)
    confirmation = await aliases.handle_confirmation(event)  # todo: rewrite with match case?

    if confirmation is True:
        fsm_data = await FSM.get_data(event=event, for_what=FOR_USER)

        user_id = event.from_id
        donor_name = fsm_data[FSMDataKey.DONOR_NAME]
        phone_number = fsm_data[FSMDataKey.PHONE_NUMBER]
        organization_name = fsm_data[FSMDataKey.ORGANIZATION_NAME]
        municipality_name = fsm_data[FSMDataKey.MUNICIPALITY_NAME]

        municipality = await Municipality.find_one(Municipality.name == municipality_name)

        logger.info(type(municipality))

        donor = Donor(
            user_id=user_id,
            name=donor_name,
            phone_number=phone_number,
            organization_name=organization_name,
            municipality=municipality,
        )
        await donor.save()

        logger.info(f"registered a new donor: {donor}")
        await event.answer("😺 Чудесно! Регистрация на акцию пройдена успешно.")
        await home.send_home(event)
        await FSM.set_state(state=HomeState.HOME, event=event, for_what=FOR_USER)

    elif confirmation is False:
        await event.answer("🤔 Что-ж, давай попробуем сначала. Будь внимательней в этот раз!)")
        await FSM.add_data(
            event=event,
            for_what=FOR_USER,
            state_data={
                FSMDataKey.PHONE_NUMBER: None,
                FSMDataKey.MUNICIPALITY_NAME: None,
                FSMDataKey.ORGANIZATION_NAME: None,
            },
        )
        await request_municipality(event)
        await FSM.set_state(state=RegistrationState.SET_MUNICIPALITY, event=event, for_what=FOR_USER)
